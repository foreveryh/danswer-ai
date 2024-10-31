from datetime import datetime
from datetime import timezone

import redis
from celery import Celery
from celery import shared_task
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from redis import Redis
from sqlalchemy.orm import Session

from danswer.background.celery.apps.app_base import task_logger
from danswer.background.celery.celery_redis import RedisConnectorDeletion
from danswer.background.celery.celery_redis import RedisConnectorIndexing
from danswer.background.celery.celery_redis import RedisConnectorPruning
from danswer.background.celery.celery_redis import RedisConnectorStop
from danswer.background.celery.tasks.shared.RedisConnectorDeletionFenceData import (
    RedisConnectorDeletionFenceData,
)
from danswer.configs.app_configs import JOB_TIMEOUT
from danswer.configs.constants import CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT
from danswer.configs.constants import DanswerRedisLocks
from danswer.db.connector_credential_pair import get_connector_credential_pair_from_id
from danswer.db.connector_credential_pair import get_connector_credential_pairs
from danswer.db.engine import get_session_with_tenant
from danswer.db.enums import ConnectorCredentialPairStatus
from danswer.db.search_settings import get_all_search_settings
from danswer.redis.redis_pool import get_redis_client


class TaskDependencyError(RuntimeError):
    """Raised to the caller to indicate dependent tasks are running that would interfere
    with connector deletion."""


@shared_task(
    name="check_for_connector_deletion_task",
    soft_time_limit=JOB_TIMEOUT,
    trail=False,
    bind=True,
)
def check_for_connector_deletion_task(self: Task, *, tenant_id: str | None) -> None:
    r = get_redis_client(tenant_id=tenant_id)

    lock_beat = r.lock(
        DanswerRedisLocks.CHECK_CONNECTOR_DELETION_BEAT_LOCK,
        timeout=CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT,
    )

    try:
        # these tasks should never overlap
        if not lock_beat.acquire(blocking=False):
            return

        # collect cc_pair_ids
        cc_pair_ids: list[int] = []
        with get_session_with_tenant(tenant_id) as db_session:
            cc_pairs = get_connector_credential_pairs(db_session)
            for cc_pair in cc_pairs:
                cc_pair_ids.append(cc_pair.id)

        # try running cleanup on the cc_pair_ids
        for cc_pair_id in cc_pair_ids:
            with get_session_with_tenant(tenant_id) as db_session:
                rcs = RedisConnectorStop(cc_pair_id)
                try:
                    try_generate_document_cc_pair_cleanup_tasks(
                        self.app, cc_pair_id, db_session, r, lock_beat, tenant_id
                    )
                except TaskDependencyError as e:
                    # this means we wanted to start deleting but dependent tasks were running
                    # Leave a stop signal to clear indexing and pruning tasks more quickly
                    task_logger.info(str(e))
                    r.set(rcs.fence_key, cc_pair_id)
                else:
                    # clear the stop signal if it exists ... no longer needed
                    r.delete(rcs.fence_key)

    except SoftTimeLimitExceeded:
        task_logger.info(
            "Soft time limit exceeded, task is being terminated gracefully."
        )
    except Exception:
        task_logger.exception(f"Unexpected exception: tenant={tenant_id}")
    finally:
        if lock_beat.owned():
            lock_beat.release()


def try_generate_document_cc_pair_cleanup_tasks(
    app: Celery,
    cc_pair_id: int,
    db_session: Session,
    r: Redis,
    lock_beat: redis.lock.Lock,
    tenant_id: str | None,
) -> int | None:
    """Returns an int if syncing is needed. The int represents the number of sync tasks generated.
    Note that syncing can still be required even if the number of sync tasks generated is zero.
    Returns None if no syncing is required.

    Will raise TaskDependencyError if dependent tasks such as indexing and pruning are
    still running. In our case, the caller reacts by setting a stop signal in Redis to
    exit those tasks as quickly as possible.
    """

    lock_beat.reacquire()

    rcd = RedisConnectorDeletion(cc_pair_id)

    # don't generate sync tasks if tasks are still pending
    if r.exists(rcd.fence_key):
        return None

    # we need to load the state of the object inside the fence
    # to avoid a race condition with db.commit/fence deletion
    # at the end of this taskset
    cc_pair = get_connector_credential_pair_from_id(cc_pair_id, db_session)
    if not cc_pair:
        return None

    if cc_pair.status != ConnectorCredentialPairStatus.DELETING:
        return None

    # set a basic fence to start
    fence_value = RedisConnectorDeletionFenceData(
        num_tasks=None,
        submitted=datetime.now(timezone.utc),
    )
    r.set(rcd.fence_key, fence_value.model_dump_json())

    try:
        # do not proceed if connector indexing or connector pruning are running
        search_settings_list = get_all_search_settings(db_session)
        for search_settings in search_settings_list:
            rci = RedisConnectorIndexing(cc_pair_id, search_settings.id)
            if r.get(rci.fence_key):
                raise TaskDependencyError(
                    f"Connector deletion - Delayed (indexing in progress): "
                    f"cc_pair={cc_pair_id} "
                    f"search_settings={search_settings.id}"
                )

        rcp = RedisConnectorPruning(cc_pair_id)
        if r.get(rcp.fence_key):
            raise TaskDependencyError(
                f"Connector deletion - Delayed (pruning in progress): "
                f"cc_pair={cc_pair_id}"
            )

        # add tasks to celery and build up the task set to monitor in redis
        r.delete(rcd.taskset_key)

        # Add all documents that need to be updated into the queue
        task_logger.info(
            f"RedisConnectorDeletion.generate_tasks starting. cc_pair={cc_pair_id}"
        )
        tasks_generated = rcd.generate_tasks(app, db_session, r, lock_beat, tenant_id)
        if tasks_generated is None:
            raise ValueError("RedisConnectorDeletion.generate_tasks returned None")
    except TaskDependencyError:
        r.delete(rcd.fence_key)
        raise
    except Exception:
        task_logger.exception("Unexpected exception")
        r.delete(rcd.fence_key)
        return None
    else:
        # Currently we are allowing the sync to proceed with 0 tasks.
        # It's possible for sets/groups to be generated initially with no entries
        # and they still need to be marked as up to date.
        # if tasks_generated == 0:
        #     return 0

        task_logger.info(
            f"RedisConnectorDeletion.generate_tasks finished. "
            f"cc_pair={cc_pair_id} tasks_generated={tasks_generated}"
        )

        # set this only after all tasks have been added
        fence_value.num_tasks = tasks_generated
        r.set(rcd.fence_key, fence_value.model_dump_json())

    return tasks_generated
