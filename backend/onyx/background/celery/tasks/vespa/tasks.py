import random
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from datetime import timezone
from http import HTTPStatus
from typing import Any
from typing import cast

import httpx
from celery import Celery
from celery import shared_task
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from celery.result import AsyncResult
from celery.states import READY_STATES
from redis import Redis
from redis.lock import Lock as RedisLock
from sqlalchemy.orm import Session
from tenacity import RetryError

from onyx.access.access import get_access_for_document
from onyx.background.celery.apps.app_base import task_logger
from onyx.background.celery.celery_redis import celery_get_queue_length
from onyx.background.celery.celery_redis import celery_get_unacked_task_ids
from onyx.background.celery.tasks.doc_permission_syncing.tasks import (
    monitor_ccpair_permissions_taskset,
)
from onyx.background.celery.tasks.pruning.tasks import monitor_ccpair_pruning_taskset
from onyx.background.celery.tasks.shared.RetryDocumentIndex import RetryDocumentIndex
from onyx.background.celery.tasks.shared.tasks import LIGHT_SOFT_TIME_LIMIT
from onyx.background.celery.tasks.shared.tasks import LIGHT_TIME_LIMIT
from onyx.configs.app_configs import JOB_TIMEOUT
from onyx.configs.app_configs import VESPA_SYNC_MAX_TASKS
from onyx.configs.constants import CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT
from onyx.configs.constants import OnyxCeleryQueues
from onyx.configs.constants import OnyxCeleryTask
from onyx.configs.constants import OnyxRedisConstants
from onyx.configs.constants import OnyxRedisLocks
from onyx.configs.constants import OnyxRedisSignals
from onyx.db.connector import fetch_connector_by_id
from onyx.db.connector_credential_pair import add_deletion_failure_message
from onyx.db.connector_credential_pair import (
    delete_connector_credential_pair__no_commit,
)
from onyx.db.connector_credential_pair import get_connector_credential_pair_from_id
from onyx.db.connector_credential_pair import get_connector_credential_pairs
from onyx.db.document import count_documents_by_needs_sync
from onyx.db.document import get_document
from onyx.db.document import get_document_ids_for_connector_credential_pair
from onyx.db.document import mark_document_as_synced
from onyx.db.document_set import delete_document_set
from onyx.db.document_set import delete_document_set_cc_pair_relationship__no_commit
from onyx.db.document_set import fetch_document_sets
from onyx.db.document_set import fetch_document_sets_for_document
from onyx.db.document_set import get_document_set_by_id
from onyx.db.document_set import mark_document_set_as_synced
from onyx.db.engine import get_session_with_tenant
from onyx.db.enums import IndexingStatus
from onyx.db.enums import SyncStatus
from onyx.db.enums import SyncType
from onyx.db.index_attempt import delete_index_attempts
from onyx.db.index_attempt import get_index_attempt
from onyx.db.index_attempt import mark_attempt_failed
from onyx.db.models import DocumentSet
from onyx.db.models import UserGroup
from onyx.db.search_settings import get_active_search_settings
from onyx.db.sync_record import cleanup_sync_records
from onyx.db.sync_record import insert_sync_record
from onyx.db.sync_record import update_sync_record_status
from onyx.document_index.factory import get_default_document_index
from onyx.document_index.interfaces import VespaDocumentFields
from onyx.httpx.httpx_pool import HttpxPool
from onyx.redis.redis_connector import RedisConnector
from onyx.redis.redis_connector_credential_pair import RedisConnectorCredentialPair
from onyx.redis.redis_connector_credential_pair import (
    RedisGlobalConnectorCredentialPair,
)
from onyx.redis.redis_connector_delete import RedisConnectorDelete
from onyx.redis.redis_connector_doc_perm_sync import RedisConnectorPermissionSync
from onyx.redis.redis_connector_index import RedisConnectorIndex
from onyx.redis.redis_connector_prune import RedisConnectorPrune
from onyx.redis.redis_document_set import RedisDocumentSet
from onyx.redis.redis_pool import get_redis_client
from onyx.redis.redis_pool import get_redis_replica_client
from onyx.redis.redis_pool import redis_lock_dump
from onyx.redis.redis_pool import SCAN_ITER_COUNT_DEFAULT
from onyx.redis.redis_usergroup import RedisUserGroup
from onyx.utils.logger import setup_logger
from onyx.utils.variable_functionality import fetch_versioned_implementation
from onyx.utils.variable_functionality import (
    fetch_versioned_implementation_with_fallback,
)
from onyx.utils.variable_functionality import global_version
from onyx.utils.variable_functionality import noop_fallback
from shared_configs.configs import MULTI_TENANT

logger = setup_logger()


# celery auto associates tasks created inside another task,
# which bloats the result metadata considerably. trail=False prevents this.
@shared_task(
    name=OnyxCeleryTask.CHECK_FOR_VESPA_SYNC_TASK,
    ignore_result=True,
    soft_time_limit=JOB_TIMEOUT,
    trail=False,
    bind=True,
)
def check_for_vespa_sync_task(self: Task, *, tenant_id: str | None) -> bool | None:
    """Runs periodically to check if any document needs syncing.
    Generates sets of tasks for Celery if syncing is needed."""
    time_start = time.monotonic()

    r = get_redis_client(tenant_id=tenant_id)

    lock_beat: RedisLock = r.lock(
        OnyxRedisLocks.CHECK_VESPA_SYNC_BEAT_LOCK,
        timeout=CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT,
    )

    # these tasks should never overlap
    if not lock_beat.acquire(blocking=False):
        return None

    try:
        with get_session_with_tenant(tenant_id) as db_session:
            try_generate_stale_document_sync_tasks(
                self.app, VESPA_SYNC_MAX_TASKS, db_session, r, lock_beat, tenant_id
            )

        # region document set scan
        lock_beat.reacquire()
        document_set_ids: list[int] = []
        with get_session_with_tenant(tenant_id) as db_session:
            # check if any document sets are not synced
            document_set_info = fetch_document_sets(
                user_id=None, db_session=db_session, include_outdated=True
            )

            for document_set, _ in document_set_info:
                document_set_ids.append(document_set.id)

        for document_set_id in document_set_ids:
            lock_beat.reacquire()
            with get_session_with_tenant(tenant_id) as db_session:
                try_generate_document_set_sync_tasks(
                    self.app, document_set_id, db_session, r, lock_beat, tenant_id
                )
        # endregion

        # check if any user groups are not synced
        if global_version.is_ee_version():
            lock_beat.reacquire()

            try:
                fetch_user_groups = fetch_versioned_implementation(
                    "onyx.db.user_group", "fetch_user_groups"
                )
            except ModuleNotFoundError:
                # Always exceptions on the MIT version, which is expected
                # We shouldn't actually get here if the ee version check works
                pass
            else:
                usergroup_ids: list[int] = []
                with get_session_with_tenant(tenant_id) as db_session:
                    user_groups = fetch_user_groups(
                        db_session=db_session, only_up_to_date=False
                    )

                    for usergroup in user_groups:
                        usergroup_ids.append(usergroup.id)

                for usergroup_id in usergroup_ids:
                    lock_beat.reacquire()
                    with get_session_with_tenant(tenant_id) as db_session:
                        try_generate_user_group_sync_tasks(
                            self.app, usergroup_id, db_session, r, lock_beat, tenant_id
                        )

    except SoftTimeLimitExceeded:
        task_logger.info(
            "Soft time limit exceeded, task is being terminated gracefully."
        )
    except Exception:
        task_logger.exception("Unexpected exception during vespa metadata sync")
    finally:
        if lock_beat.owned():
            lock_beat.release()
        else:
            task_logger.error(
                "check_for_vespa_sync_task - Lock not owned on completion: "
                f"tenant={tenant_id}"
            )
            redis_lock_dump(lock_beat, r)

    time_elapsed = time.monotonic() - time_start
    task_logger.debug(f"check_for_vespa_sync_task finished: elapsed={time_elapsed:.2f}")
    return True


def try_generate_stale_document_sync_tasks(
    celery_app: Celery,
    max_tasks: int,
    db_session: Session,
    r: Redis,
    lock_beat: RedisLock,
    tenant_id: str | None,
) -> int | None:
    # the fence is up, do nothing

    redis_global_ccpair = RedisGlobalConnectorCredentialPair(r)
    if redis_global_ccpair.fenced:
        return None

    redis_global_ccpair.delete_taskset()

    # add tasks to celery and build up the task set to monitor in redis
    stale_doc_count = count_documents_by_needs_sync(db_session)
    if stale_doc_count == 0:
        return None

    task_logger.info(
        f"Stale documents found (at least {stale_doc_count}). Generating sync tasks by cc pair."
    )

    task_logger.info(
        "RedisConnector.generate_tasks starting by cc_pair. "
        "Documents spanning multiple cc_pairs will only be synced once."
    )

    docs_to_skip: set[str] = set()

    # rkuo: we could technically sync all stale docs in one big pass.
    # but I feel it's more understandable to group the docs by cc_pair
    total_tasks_generated = 0
    tasks_remaining = max_tasks
    cc_pairs = get_connector_credential_pairs(db_session)
    for cc_pair in cc_pairs:
        lock_beat.reacquire()

        rc = RedisConnectorCredentialPair(tenant_id, cc_pair.id)
        rc.set_skip_docs(docs_to_skip)
        result = rc.generate_tasks(
            tasks_remaining, celery_app, db_session, r, lock_beat, tenant_id
        )

        if result is None:
            continue

        if result[1] == 0:
            continue

        task_logger.info(
            f"RedisConnector.generate_tasks finished for single cc_pair. "
            f"cc_pair={cc_pair.id} tasks_generated={result[0]} tasks_possible={result[1]}"
        )

        total_tasks_generated += result[0]
        tasks_remaining -= result[0]
        if tasks_remaining <= 0:
            break

    if tasks_remaining <= 0:
        task_logger.info(
            f"RedisConnector.generate_tasks reached the task generation limit: "
            f"total_tasks_generated={total_tasks_generated} max_tasks={max_tasks}"
        )
    else:
        task_logger.info(
            f"RedisConnector.generate_tasks finished for all cc_pairs. total_tasks_generated={total_tasks_generated}"
        )

    redis_global_ccpair.set_fence(total_tasks_generated)
    return total_tasks_generated


def try_generate_document_set_sync_tasks(
    celery_app: Celery,
    document_set_id: int,
    db_session: Session,
    r: Redis,
    lock_beat: RedisLock,
    tenant_id: str | None,
) -> int | None:
    lock_beat.reacquire()

    rds = RedisDocumentSet(tenant_id, document_set_id)

    # don't generate document set sync tasks if tasks are still pending
    if rds.fenced:
        return None

    # don't generate sync tasks if we're up to date
    # race condition with the monitor/cleanup function if we use a cached result!
    document_set = get_document_set_by_id(
        db_session=db_session,
        document_set_id=document_set_id,
    )
    if not document_set:
        return None

    if document_set.is_up_to_date:
        # there should be no in-progress sync records if this is up to date
        # clean it up just in case things got into a bad state
        cleanup_sync_records(
            db_session=db_session,
            entity_id=document_set_id,
            sync_type=SyncType.DOCUMENT_SET,
        )
        return None

    # add tasks to celery and build up the task set to monitor in redis
    r.delete(rds.taskset_key)

    task_logger.info(
        f"RedisDocumentSet.generate_tasks starting. document_set_id={document_set.id}"
    )

    # Add all documents that need to be updated into the queue
    result = rds.generate_tasks(
        VESPA_SYNC_MAX_TASKS, celery_app, db_session, r, lock_beat, tenant_id
    )
    if result is None:
        return None

    tasks_generated = result[0]
    # Currently we are allowing the sync to proceed with 0 tasks.
    # It's possible for sets/groups to be generated initially with no entries
    # and they still need to be marked as up to date.
    # if tasks_generated == 0:
    #     return 0

    task_logger.info(
        f"RedisDocumentSet.generate_tasks finished. "
        f"document_set={document_set.id} tasks_generated={tasks_generated}"
    )

    # create before setting fence to avoid race condition where the monitoring
    # task updates the sync record before it is created
    try:
        insert_sync_record(
            db_session=db_session,
            entity_id=document_set_id,
            sync_type=SyncType.DOCUMENT_SET,
        )
    except Exception:
        task_logger.exception("insert_sync_record exceptioned.")

    # set this only after all tasks have been added
    rds.set_fence(tasks_generated)
    return tasks_generated


def try_generate_user_group_sync_tasks(
    celery_app: Celery,
    usergroup_id: int,
    db_session: Session,
    r: Redis,
    lock_beat: RedisLock,
    tenant_id: str | None,
) -> int | None:
    lock_beat.reacquire()

    rug = RedisUserGroup(tenant_id, usergroup_id)
    if rug.fenced:
        # don't generate sync tasks if tasks are still pending
        return None

    # race condition with the monitor/cleanup function if we use a cached result!
    fetch_user_group = cast(
        Callable[[Session, int], UserGroup | None],
        fetch_versioned_implementation("onyx.db.user_group", "fetch_user_group"),
    )

    usergroup = fetch_user_group(db_session, usergroup_id)
    if not usergroup:
        return None

    if usergroup.is_up_to_date:
        # there should be no in-progress sync records if this is up to date
        # clean it up just in case things got into a bad state
        cleanup_sync_records(
            db_session=db_session,
            entity_id=usergroup_id,
            sync_type=SyncType.USER_GROUP,
        )
        return None

    # add tasks to celery and build up the task set to monitor in redis
    r.delete(rug.taskset_key)

    # Add all documents that need to be updated into the queue
    task_logger.info(
        f"RedisUserGroup.generate_tasks starting. usergroup_id={usergroup.id}"
    )
    result = rug.generate_tasks(
        VESPA_SYNC_MAX_TASKS, celery_app, db_session, r, lock_beat, tenant_id
    )
    if result is None:
        return None

    tasks_generated = result[0]
    # Currently we are allowing the sync to proceed with 0 tasks.
    # It's possible for sets/groups to be generated initially with no entries
    # and they still need to be marked as up to date.
    # if tasks_generated == 0:
    #     return 0

    task_logger.info(
        f"RedisUserGroup.generate_tasks finished. "
        f"usergroup={usergroup.id} tasks_generated={tasks_generated}"
    )

    # create before setting fence to avoid race condition where the monitoring
    # task updates the sync record before it is created
    try:
        insert_sync_record(
            db_session=db_session,
            entity_id=usergroup_id,
            sync_type=SyncType.USER_GROUP,
        )
    except Exception:
        task_logger.exception("insert_sync_record exceptioned.")

    # set this only after all tasks have been added
    rug.set_fence(tasks_generated)

    return tasks_generated


def monitor_connector_taskset(r: Redis) -> None:
    redis_global_ccpair = RedisGlobalConnectorCredentialPair(r)
    initial_count = redis_global_ccpair.payload
    if initial_count is None:
        return

    remaining = redis_global_ccpair.get_remaining()
    task_logger.info(
        f"Stale document sync progress: remaining={remaining} initial={initial_count}"
    )
    if remaining == 0:
        redis_global_ccpair.reset()
        task_logger.info(f"Successfully synced stale documents. count={initial_count}")


def monitor_document_set_taskset(
    tenant_id: str | None, key_bytes: bytes, r: Redis, db_session: Session
) -> None:
    fence_key = key_bytes.decode("utf-8")
    document_set_id_str = RedisDocumentSet.get_id_from_fence_key(fence_key)
    if document_set_id_str is None:
        task_logger.warning(f"could not parse document set id from {fence_key}")
        return

    document_set_id = int(document_set_id_str)

    rds = RedisDocumentSet(tenant_id, document_set_id)
    if not rds.fenced:
        return

    initial_count = rds.payload
    if initial_count is None:
        return

    count = cast(int, r.scard(rds.taskset_key))
    task_logger.info(
        f"Document set sync progress: document_set={document_set_id} "
        f"remaining={count} initial={initial_count}"
    )
    if count > 0:
        update_sync_record_status(
            db_session=db_session,
            entity_id=document_set_id,
            sync_type=SyncType.DOCUMENT_SET,
            sync_status=SyncStatus.IN_PROGRESS,
            num_docs_synced=count,
        )
        return

    document_set = cast(
        DocumentSet,
        get_document_set_by_id(db_session=db_session, document_set_id=document_set_id),
    )  # casting since we "know" a document set with this ID exists
    if document_set:
        if not document_set.connector_credential_pairs:
            # if there are no connectors, then delete the document set.
            delete_document_set(document_set_row=document_set, db_session=db_session)
            task_logger.info(
                f"Successfully deleted document set: document_set={document_set_id}"
            )
        else:
            mark_document_set_as_synced(document_set_id, db_session)
            task_logger.info(
                f"Successfully synced document set: document_set={document_set_id}"
            )
        update_sync_record_status(
            db_session=db_session,
            entity_id=document_set_id,
            sync_type=SyncType.DOCUMENT_SET,
            sync_status=SyncStatus.SUCCESS,
            num_docs_synced=initial_count,
        )

    rds.reset()


def monitor_connector_deletion_taskset(
    tenant_id: str | None, key_bytes: bytes, r: Redis
) -> None:
    fence_key = key_bytes.decode("utf-8")
    cc_pair_id_str = RedisConnector.get_id_from_fence_key(fence_key)
    if cc_pair_id_str is None:
        task_logger.warning(f"could not parse cc_pair_id from {fence_key}")
        return

    cc_pair_id = int(cc_pair_id_str)

    redis_connector = RedisConnector(tenant_id, cc_pair_id)

    fence_data = redis_connector.delete.payload
    if not fence_data:
        task_logger.warning(
            f"Connector deletion - fence payload invalid: cc_pair={cc_pair_id}"
        )
        return

    if fence_data.num_tasks is None:
        # the fence is setting up but isn't ready yet
        return

    remaining = redis_connector.delete.get_remaining()
    task_logger.info(
        f"Connector deletion progress: cc_pair={cc_pair_id} remaining={remaining} initial={fence_data.num_tasks}"
    )
    if remaining > 0:
        with get_session_with_tenant(tenant_id) as db_session:
            update_sync_record_status(
                db_session=db_session,
                entity_id=cc_pair_id,
                sync_type=SyncType.CONNECTOR_DELETION,
                sync_status=SyncStatus.IN_PROGRESS,
                num_docs_synced=remaining,
            )
        return

    with get_session_with_tenant(tenant_id) as db_session:
        cc_pair = get_connector_credential_pair_from_id(
            db_session=db_session,
            cc_pair_id=cc_pair_id,
        )
        if not cc_pair:
            task_logger.warning(
                f"Connector deletion - cc_pair not found: cc_pair={cc_pair_id}"
            )
            return

        try:
            doc_ids = get_document_ids_for_connector_credential_pair(
                db_session, cc_pair.connector_id, cc_pair.credential_id
            )
            if len(doc_ids) > 0:
                # NOTE(rkuo): if this happens, documents somehow got added while
                # deletion was in progress. Likely a bug gating off pruning and indexing
                # work before deletion starts.
                task_logger.warning(
                    "Connector deletion - documents still found after taskset completion. "
                    "Clearing the current deletion attempt and allowing deletion to restart: "
                    f"cc_pair={cc_pair_id} "
                    f"docs_deleted={fence_data.num_tasks} "
                    f"docs_remaining={len(doc_ids)}"
                )

                # We don't want to waive off why we get into this state, but resetting
                # our attempt and letting the deletion restart is a good way to recover
                redis_connector.delete.reset()
                raise RuntimeError(
                    "Connector deletion - documents still found after taskset completion"
                )

            # clean up the rest of the related Postgres entities
            # index attempts
            delete_index_attempts(
                db_session=db_session,
                cc_pair_id=cc_pair_id,
            )

            # document sets
            delete_document_set_cc_pair_relationship__no_commit(
                db_session=db_session,
                connector_id=cc_pair.connector_id,
                credential_id=cc_pair.credential_id,
            )

            # user groups
            cleanup_user_groups = fetch_versioned_implementation_with_fallback(
                "onyx.db.user_group",
                "delete_user_group_cc_pair_relationship__no_commit",
                noop_fallback,
            )
            cleanup_user_groups(
                cc_pair_id=cc_pair_id,
                db_session=db_session,
            )

            # finally, delete the cc-pair
            delete_connector_credential_pair__no_commit(
                db_session=db_session,
                connector_id=cc_pair.connector_id,
                credential_id=cc_pair.credential_id,
            )
            # if there are no credentials left, delete the connector
            connector = fetch_connector_by_id(
                db_session=db_session,
                connector_id=cc_pair.connector_id,
            )
            if not connector or not len(connector.credentials):
                task_logger.info(
                    "Connector deletion - Found no credentials left for connector, deleting connector"
                )
                db_session.delete(connector)
            db_session.commit()

            update_sync_record_status(
                db_session=db_session,
                entity_id=cc_pair_id,
                sync_type=SyncType.CONNECTOR_DELETION,
                sync_status=SyncStatus.SUCCESS,
                num_docs_synced=fence_data.num_tasks,
            )

        except Exception as e:
            db_session.rollback()
            stack_trace = traceback.format_exc()
            error_message = f"Error: {str(e)}\n\nStack Trace:\n{stack_trace}"
            add_deletion_failure_message(db_session, cc_pair_id, error_message)

            update_sync_record_status(
                db_session=db_session,
                entity_id=cc_pair_id,
                sync_type=SyncType.CONNECTOR_DELETION,
                sync_status=SyncStatus.FAILED,
                num_docs_synced=fence_data.num_tasks,
            )

            task_logger.exception(
                f"Connector deletion exceptioned: "
                f"cc_pair={cc_pair_id} connector={cc_pair.connector_id} credential={cc_pair.credential_id}"
            )
            raise e

    task_logger.info(
        f"Connector deletion succeeded: "
        f"cc_pair={cc_pair_id} "
        f"connector={cc_pair.connector_id} "
        f"credential={cc_pair.credential_id} "
        f"docs_deleted={fence_data.num_tasks}"
    )

    redis_connector.delete.reset()


def monitor_ccpair_indexing_taskset(
    tenant_id: str | None, key_bytes: bytes, r: Redis, db_session: Session
) -> None:
    # if the fence doesn't exist, there's nothing to do
    fence_key = key_bytes.decode("utf-8")
    composite_id = RedisConnector.get_id_from_fence_key(fence_key)
    if composite_id is None:
        task_logger.warning(
            f"Connector indexing: could not parse composite_id from {fence_key}"
        )
        return

    # parse out metadata and initialize the helper class with it
    parts = composite_id.split("/")
    if len(parts) != 2:
        return

    cc_pair_id = int(parts[0])
    search_settings_id = int(parts[1])

    redis_connector = RedisConnector(tenant_id, cc_pair_id)
    redis_connector_index = redis_connector.new_index(search_settings_id)
    if not redis_connector_index.fenced:
        return

    payload = redis_connector_index.payload
    if not payload:
        return

    elapsed_started_str = None
    if payload.started:
        elapsed_started = datetime.now(timezone.utc) - payload.started
        elapsed_started_str = f"{elapsed_started.total_seconds():.2f}"

    elapsed_submitted = datetime.now(timezone.utc) - payload.submitted

    progress = redis_connector_index.get_progress()
    if progress is not None:
        task_logger.info(
            f"Connector indexing progress: "
            f"attempt={payload.index_attempt_id} "
            f"cc_pair={cc_pair_id} "
            f"search_settings={search_settings_id} "
            f"progress={progress} "
            f"elapsed_submitted={elapsed_submitted.total_seconds():.2f} "
            f"elapsed_started={elapsed_started_str}"
        )

    if payload.index_attempt_id is None or payload.celery_task_id is None:
        # the task is still setting up
        return

    # never use any blocking methods on the result from inside a task!
    result: AsyncResult = AsyncResult(payload.celery_task_id)

    # inner/outer/inner double check pattern to avoid race conditions when checking for
    # bad state

    # Verify: if the generator isn't complete, the task must not be in READY state
    # inner = get_completion / generator_complete not signaled
    # outer = result.state in READY state
    status_int = redis_connector_index.get_completion()
    if status_int is None:  # inner signal not set ... possible error
        task_state = result.state
        if (
            task_state in READY_STATES
        ):  # outer signal in terminal state ... possible error
            # Now double check!
            if redis_connector_index.get_completion() is None:
                # inner signal still not set (and cannot change when outer result_state is READY)
                # Task is finished but generator complete isn't set.
                # We have a problem! Worker may have crashed.
                task_result = str(result.result)
                task_traceback = str(result.traceback)

                msg = (
                    f"Connector indexing aborted or exceptioned: "
                    f"attempt={payload.index_attempt_id} "
                    f"celery_task={payload.celery_task_id} "
                    f"cc_pair={cc_pair_id} "
                    f"search_settings={search_settings_id} "
                    f"elapsed_submitted={elapsed_submitted.total_seconds():.2f} "
                    f"result.state={task_state} "
                    f"result.result={task_result} "
                    f"result.traceback={task_traceback}"
                )
                task_logger.warning(msg)

                try:
                    index_attempt = get_index_attempt(
                        db_session, payload.index_attempt_id
                    )
                    if index_attempt:
                        if (
                            index_attempt.status != IndexingStatus.CANCELED
                            and index_attempt.status != IndexingStatus.FAILED
                        ):
                            mark_attempt_failed(
                                index_attempt_id=payload.index_attempt_id,
                                db_session=db_session,
                                failure_reason=msg,
                            )
                except Exception:
                    task_logger.exception(
                        "Connector indexing - Transient exception marking index attempt as failed: "
                        f"attempt={payload.index_attempt_id} "
                        f"tenant={tenant_id} "
                        f"cc_pair={cc_pair_id} "
                        f"search_settings={search_settings_id}"
                    )

                redis_connector_index.reset()
        return

    if redis_connector_index.watchdog_signaled():
        # if the generator is complete, don't clean up until the watchdog has exited
        task_logger.info(
            f"Connector indexing - Delaying finalization until watchdog has exited: "
            f"attempt={payload.index_attempt_id} "
            f"cc_pair={cc_pair_id} "
            f"search_settings={search_settings_id} "
            f"progress={progress} "
            f"elapsed_submitted={elapsed_submitted.total_seconds():.2f} "
            f"elapsed_started={elapsed_started_str}"
        )

        return

    status_enum = HTTPStatus(status_int)

    task_logger.info(
        f"Connector indexing finished: "
        f"attempt={payload.index_attempt_id} "
        f"cc_pair={cc_pair_id} "
        f"search_settings={search_settings_id} "
        f"progress={progress} "
        f"status={status_enum.name} "
        f"elapsed_submitted={elapsed_submitted.total_seconds():.2f} "
        f"elapsed_started={elapsed_started_str}"
    )

    redis_connector_index.reset()


@shared_task(
    name=OnyxCeleryTask.MONITOR_VESPA_SYNC,
    ignore_result=True,
    soft_time_limit=300,
    bind=True,
)
def monitor_vespa_sync(self: Task, tenant_id: str | None) -> bool | None:
    """This is a celery beat task that monitors and finalizes various long running tasks.

    The name monitor_vespa_sync is a bit of a misnomer since it checks many different tasks
    now. Should change that at some point.

    It scans for fence values and then gets the counts of any associated tasksets.
    For many tasks, the count is 0, that means all tasks finished and we should clean up.

    This task lock timeout is CELERY_METADATA_SYNC_BEAT_LOCK_TIMEOUT seconds, so don't
    do anything too expensive in this function!

    Returns True if the task actually did work, False if it exited early to prevent overlap
    """
    task_logger.info(f"monitor_vespa_sync starting: tenant={tenant_id}")

    time_start = time.monotonic()

    r = get_redis_client(tenant_id=tenant_id)

    # Replica usage notes
    #
    # False negatives are OK. (aka fail to to see a key that exists on the master).
    # We simply skip the monitoring work and it will be caught on the next pass.
    #
    # False positives are not OK, and are possible if we clear a fence on the master and
    # then read from the replica. In this case, monitoring work could be done on a fence
    # that no longer exists. To avoid this, we scan from the replica, but double check
    # the result on the master.
    r_replica = get_redis_replica_client(tenant_id=tenant_id)

    lock_beat: RedisLock = r.lock(
        OnyxRedisLocks.MONITOR_VESPA_SYNC_BEAT_LOCK,
        timeout=CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT,
    )

    # prevent overlapping tasks
    if not lock_beat.acquire(blocking=False):
        return None

    try:
        # print current queue lengths
        time.monotonic()
        # we don't need every tenant polling redis for this info.
        if not MULTI_TENANT or random.randint(1, 10) == 10:
            r_celery = self.app.broker_connection().channel().client  # type: ignore
            n_celery = celery_get_queue_length("celery", r_celery)
            n_indexing = celery_get_queue_length(
                OnyxCeleryQueues.CONNECTOR_INDEXING, r_celery
            )
            n_sync = celery_get_queue_length(
                OnyxCeleryQueues.VESPA_METADATA_SYNC, r_celery
            )
            n_deletion = celery_get_queue_length(
                OnyxCeleryQueues.CONNECTOR_DELETION, r_celery
            )
            n_pruning = celery_get_queue_length(
                OnyxCeleryQueues.CONNECTOR_PRUNING, r_celery
            )
            n_permissions_sync = celery_get_queue_length(
                OnyxCeleryQueues.CONNECTOR_DOC_PERMISSIONS_SYNC, r_celery
            )
            n_external_group_sync = celery_get_queue_length(
                OnyxCeleryQueues.CONNECTOR_EXTERNAL_GROUP_SYNC, r_celery
            )
            n_permissions_upsert = celery_get_queue_length(
                OnyxCeleryQueues.DOC_PERMISSIONS_UPSERT, r_celery
            )

            prefetched = celery_get_unacked_task_ids(
                OnyxCeleryQueues.CONNECTOR_INDEXING, r_celery
            )

            task_logger.info(
                f"Queue lengths: celery={n_celery} "
                f"indexing={n_indexing} "
                f"indexing_prefetched={len(prefetched)} "
                f"sync={n_sync} "
                f"deletion={n_deletion} "
                f"pruning={n_pruning} "
                f"permissions_sync={n_permissions_sync} "
                f"external_group_sync={n_external_group_sync} "
                f"permissions_upsert={n_permissions_upsert} "
            )

        # we want to run this less frequently than the overall task
        if not r.exists(OnyxRedisSignals.BLOCK_BUILD_FENCE_LOOKUP_TABLE):
            # build a lookup table of existing fences
            # this is just a migration concern and should be unnecessary once
            # lookup tables are rolled out
            for key_bytes in r_replica.scan_iter(count=SCAN_ITER_COUNT_DEFAULT):
                if is_fence(key_bytes) and not r.sismember(
                    OnyxRedisConstants.ACTIVE_FENCES, key_bytes
                ):
                    logger.warning(f"Adding {key_bytes} to the lookup table.")
                    r.sadd(OnyxRedisConstants.ACTIVE_FENCES, key_bytes)

            r.set(OnyxRedisSignals.BLOCK_BUILD_FENCE_LOOKUP_TABLE, 1, ex=300)

        # use a lookup table to find active fences. We still have to verify the fence
        # exists since it is an optimization and not the source of truth.
        keys = cast(set[Any], r_replica.smembers(OnyxRedisConstants.ACTIVE_FENCES))
        for key in keys:
            key_bytes = cast(bytes, key)

            if not r.exists(key_bytes):
                r.srem(OnyxRedisConstants.ACTIVE_FENCES, key_bytes)
                continue

            key_str = key_bytes.decode("utf-8")
            if key_str == RedisGlobalConnectorCredentialPair.FENCE_KEY:
                monitor_connector_taskset(r)
            elif key_str.startswith(RedisDocumentSet.FENCE_PREFIX):
                with get_session_with_tenant(tenant_id) as db_session:
                    monitor_document_set_taskset(tenant_id, key_bytes, r, db_session)
            elif key_str.startswith(RedisUserGroup.FENCE_PREFIX):
                monitor_usergroup_taskset = (
                    fetch_versioned_implementation_with_fallback(
                        "onyx.background.celery.tasks.vespa.tasks",
                        "monitor_usergroup_taskset",
                        noop_fallback,
                    )
                )
                with get_session_with_tenant(tenant_id) as db_session:
                    monitor_usergroup_taskset(tenant_id, key_bytes, r, db_session)
            elif key_str.startswith(RedisConnectorDelete.FENCE_PREFIX):
                monitor_connector_deletion_taskset(tenant_id, key_bytes, r)
            elif key_str.startswith(RedisConnectorPrune.FENCE_PREFIX):
                with get_session_with_tenant(tenant_id) as db_session:
                    monitor_ccpair_pruning_taskset(tenant_id, key_bytes, r, db_session)
            elif key_str.startswith(RedisConnectorIndex.FENCE_PREFIX):
                with get_session_with_tenant(tenant_id) as db_session:
                    monitor_ccpair_indexing_taskset(tenant_id, key_bytes, r, db_session)
            elif key_str.startswith(RedisConnectorPermissionSync.FENCE_PREFIX):
                with get_session_with_tenant(tenant_id) as db_session:
                    monitor_ccpair_permissions_taskset(
                        tenant_id, key_bytes, r, db_session
                    )
            else:
                pass
    except SoftTimeLimitExceeded:
        task_logger.info(
            "Soft time limit exceeded, task is being terminated gracefully."
        )
        return False
    except Exception:
        task_logger.exception("monitor_vespa_sync exceptioned.")
        return False
    finally:
        if lock_beat.owned():
            lock_beat.release()
        else:
            task_logger.error(
                "monitor_vespa_sync - Lock not owned on completion: "
                f"tenant={tenant_id}"
                # f"timings={timings}"
            )
            redis_lock_dump(lock_beat, r)

    time_elapsed = time.monotonic() - time_start
    task_logger.info(f"monitor_vespa_sync finished: elapsed={time_elapsed:.2f}")
    return True


@shared_task(
    name=OnyxCeleryTask.VESPA_METADATA_SYNC_TASK,
    bind=True,
    soft_time_limit=LIGHT_SOFT_TIME_LIMIT,
    time_limit=LIGHT_TIME_LIMIT,
    max_retries=3,
)
def vespa_metadata_sync_task(
    self: Task, document_id: str, tenant_id: str | None
) -> bool:
    start = time.monotonic()

    try:
        with get_session_with_tenant(tenant_id) as db_session:
            active_search_settings = get_active_search_settings(db_session)
            doc_index = get_default_document_index(
                search_settings=active_search_settings.primary,
                secondary_search_settings=active_search_settings.secondary,
                httpx_client=HttpxPool.get("vespa"),
            )

            retry_index = RetryDocumentIndex(doc_index)

            doc = get_document(document_id, db_session)
            if not doc:
                return False

            # document set sync
            doc_sets = fetch_document_sets_for_document(document_id, db_session)
            update_doc_sets: set[str] = set(doc_sets)

            # User group sync
            doc_access = get_access_for_document(
                document_id=document_id, db_session=db_session
            )

            fields = VespaDocumentFields(
                document_sets=update_doc_sets,
                access=doc_access,
                boost=doc.boost,
                hidden=doc.hidden,
            )

            # update Vespa. OK if doc doesn't exist. Raises exception otherwise.
            chunks_affected = retry_index.update_single(
                document_id,
                tenant_id=tenant_id,
                chunk_count=doc.chunk_count,
                fields=fields,
            )

            # update db last. Worst case = we crash right before this and
            # the sync might repeat again later
            mark_document_as_synced(document_id, db_session)

            elapsed = time.monotonic() - start
            task_logger.info(
                f"doc={document_id} "
                f"action=sync "
                f"chunks={chunks_affected} "
                f"elapsed={elapsed:.2f}"
            )
    except SoftTimeLimitExceeded:
        task_logger.info(f"SoftTimeLimitExceeded exception. doc={document_id}")
        return False
    except Exception as ex:
        e: Exception | None = None
        if isinstance(ex, RetryError):
            task_logger.warning(
                f"Tenacity retry failed: num_attempts={ex.last_attempt.attempt_number}"
            )

            # only set the inner exception if it is of type Exception
            e_temp = ex.last_attempt.exception()
            if isinstance(e_temp, Exception):
                e = e_temp
        else:
            e = ex

        if isinstance(e, httpx.HTTPStatusError):
            if e.response.status_code == HTTPStatus.BAD_REQUEST:
                task_logger.exception(
                    f"Non-retryable HTTPStatusError: "
                    f"doc={document_id} "
                    f"status={e.response.status_code}"
                )
            return False

        task_logger.exception(
            f"Unexpected exception during vespa metadata sync: doc={document_id}"
        )

        # Exponential backoff from 2^4 to 2^6 ... i.e. 16, 32, 64
        countdown = 2 ** (self.request.retries + 4)
        self.retry(exc=e, countdown=countdown)

    return True


def is_fence(key_bytes: bytes) -> bool:
    key_str = key_bytes.decode("utf-8")
    if key_str == RedisGlobalConnectorCredentialPair.FENCE_KEY:
        return True
    if key_str.startswith(RedisDocumentSet.FENCE_PREFIX):
        return True
    if key_str.startswith(RedisUserGroup.FENCE_PREFIX):
        return True
    if key_str.startswith(RedisConnectorDelete.FENCE_PREFIX):
        return True
    if key_str.startswith(RedisConnectorPrune.FENCE_PREFIX):
        return True
    if key_str.startswith(RedisConnectorIndex.FENCE_PREFIX):
        return True
    if key_str.startswith(RedisConnectorPermissionSync.FENCE_PREFIX):
        return True

    return False
