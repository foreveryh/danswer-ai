import time
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from time import sleep
from typing import Any
from typing import cast
from uuid import uuid4

from celery import Celery
from celery import shared_task
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import LockError
from redis.lock import Lock as RedisLock
from sqlalchemy.orm import Session

from ee.onyx.db.connector_credential_pair import get_all_auto_sync_cc_pairs
from ee.onyx.db.document import upsert_document_external_perms
from ee.onyx.external_permissions.sync_params import DOC_PERMISSION_SYNC_PERIODS
from ee.onyx.external_permissions.sync_params import DOC_PERMISSIONS_FUNC_MAP
from ee.onyx.external_permissions.sync_params import (
    DOC_SOURCE_TO_CHUNK_CENSORING_FUNCTION,
)
from onyx.access.models import DocExternalAccess
from onyx.background.celery.apps.app_base import task_logger
from onyx.background.celery.celery_redis import celery_find_task
from onyx.background.celery.celery_redis import celery_get_queue_length
from onyx.background.celery.celery_redis import celery_get_queued_task_ids
from onyx.background.celery.celery_redis import celery_get_unacked_task_ids
from onyx.configs.app_configs import JOB_TIMEOUT
from onyx.configs.constants import CELERY_GENERIC_BEAT_LOCK_TIMEOUT
from onyx.configs.constants import CELERY_PERMISSIONS_SYNC_LOCK_TIMEOUT
from onyx.configs.constants import CELERY_TASK_WAIT_FOR_FENCE_TIMEOUT
from onyx.configs.constants import DANSWER_REDIS_FUNCTION_LOCK_PREFIX
from onyx.configs.constants import DocumentSource
from onyx.configs.constants import OnyxCeleryPriority
from onyx.configs.constants import OnyxCeleryQueues
from onyx.configs.constants import OnyxCeleryTask
from onyx.configs.constants import OnyxRedisConstants
from onyx.configs.constants import OnyxRedisLocks
from onyx.configs.constants import OnyxRedisSignals
from onyx.db.connector import mark_cc_pair_as_permissions_synced
from onyx.db.connector_credential_pair import get_connector_credential_pair_from_id
from onyx.db.document import upsert_document_by_connector_credential_pair
from onyx.db.engine import get_session_with_tenant
from onyx.db.enums import AccessType
from onyx.db.enums import ConnectorCredentialPairStatus
from onyx.db.enums import SyncStatus
from onyx.db.enums import SyncType
from onyx.db.models import ConnectorCredentialPair
from onyx.db.sync_record import insert_sync_record
from onyx.db.sync_record import update_sync_record_status
from onyx.db.users import batch_add_ext_perm_user_if_not_exists
from onyx.indexing.indexing_heartbeat import IndexingHeartbeatInterface
from onyx.redis.redis_connector import RedisConnector
from onyx.redis.redis_connector_doc_perm_sync import RedisConnectorPermissionSync
from onyx.redis.redis_connector_doc_perm_sync import RedisConnectorPermissionSyncPayload
from onyx.redis.redis_pool import get_redis_client
from onyx.redis.redis_pool import get_redis_replica_client
from onyx.redis.redis_pool import redis_lock_dump
from onyx.server.utils import make_short_id
from onyx.utils.logger import doc_permission_sync_ctx
from onyx.utils.logger import LoggerContextVars
from onyx.utils.logger import setup_logger


logger = setup_logger()


DOCUMENT_PERMISSIONS_UPDATE_MAX_RETRIES = 3


# 5 seconds more than RetryDocumentIndex STOP_AFTER+MAX_WAIT
LIGHT_SOFT_TIME_LIMIT = 105
LIGHT_TIME_LIMIT = LIGHT_SOFT_TIME_LIMIT + 15


"""Jobs / utils for kicking off doc permissions sync tasks."""


def _is_external_doc_permissions_sync_due(cc_pair: ConnectorCredentialPair) -> bool:
    """Returns boolean indicating if external doc permissions sync is due."""

    if cc_pair.access_type != AccessType.SYNC:
        return False

    # skip doc permissions sync if not active
    if cc_pair.status != ConnectorCredentialPairStatus.ACTIVE:
        return False

    if cc_pair.status == ConnectorCredentialPairStatus.DELETING:
        return False

    # If the last sync is None, it has never been run so we run the sync
    last_perm_sync = cc_pair.last_time_perm_sync
    if last_perm_sync is None:
        return True

    source_sync_period = DOC_PERMISSION_SYNC_PERIODS.get(cc_pair.connector.source)

    # If RESTRICTED_FETCH_PERIOD[source] is None, we always run the sync.
    if not source_sync_period:
        return True

    # If the last sync is greater than the full fetch period, we run the sync
    next_sync = last_perm_sync + timedelta(seconds=source_sync_period)
    if datetime.now(timezone.utc) >= next_sync:
        return True

    return False


@shared_task(
    name=OnyxCeleryTask.CHECK_FOR_DOC_PERMISSIONS_SYNC,
    ignore_result=True,
    soft_time_limit=JOB_TIMEOUT,
    bind=True,
)
def check_for_doc_permissions_sync(self: Task, *, tenant_id: str | None) -> bool | None:
    # TODO(rkuo): merge into check function after lookup table for fences is added

    # we need to use celery's redis client to access its redis data
    # (which lives on a different db number)
    r = get_redis_client(tenant_id=tenant_id)
    r_replica = get_redis_replica_client(tenant_id=tenant_id)
    r_celery: Redis = self.app.broker_connection().channel().client  # type: ignore

    lock_beat: RedisLock = r.lock(
        OnyxRedisLocks.CHECK_CONNECTOR_DOC_PERMISSIONS_SYNC_BEAT_LOCK,
        timeout=CELERY_GENERIC_BEAT_LOCK_TIMEOUT,
    )

    # these tasks should never overlap
    if not lock_beat.acquire(blocking=False):
        return None

    try:
        # get all cc pairs that need to be synced
        cc_pair_ids_to_sync: list[int] = []
        with get_session_with_tenant(tenant_id) as db_session:
            cc_pairs = get_all_auto_sync_cc_pairs(db_session)

            for cc_pair in cc_pairs:
                if _is_external_doc_permissions_sync_due(cc_pair):
                    cc_pair_ids_to_sync.append(cc_pair.id)

        lock_beat.reacquire()
        for cc_pair_id in cc_pair_ids_to_sync:
            payload_id = try_creating_permissions_sync_task(
                self.app, cc_pair_id, r, tenant_id
            )
            if not payload_id:
                continue

            task_logger.info(
                f"Permissions sync queued: cc_pair={cc_pair_id} id={payload_id}"
            )

        # we want to run this less frequently than the overall task
        lock_beat.reacquire()
        if not r.exists(OnyxRedisSignals.BLOCK_VALIDATE_PERMISSION_SYNC_FENCES):
            # clear any permission fences that don't have associated celery tasks in progress
            # tasks can be in the queue in redis, in reserved tasks (prefetched by the worker),
            # or be currently executing
            try:
                validate_permission_sync_fences(
                    tenant_id, r, r_replica, r_celery, lock_beat
                )
            except Exception:
                task_logger.exception(
                    "Exception while validating permission sync fences"
                )

            r.set(OnyxRedisSignals.BLOCK_VALIDATE_PERMISSION_SYNC_FENCES, 1, ex=300)
    except SoftTimeLimitExceeded:
        task_logger.info(
            "Soft time limit exceeded, task is being terminated gracefully."
        )
    except Exception:
        task_logger.exception(f"Unexpected exception: tenant={tenant_id}")
    finally:
        if lock_beat.owned():
            lock_beat.release()

    return True


def try_creating_permissions_sync_task(
    app: Celery,
    cc_pair_id: int,
    r: Redis,
    tenant_id: str | None,
) -> str | None:
    """Returns a randomized payload id on success.
    Returns None if no syncing is required."""
    LOCK_TIMEOUT = 30

    payload_id: str | None = None

    redis_connector = RedisConnector(tenant_id, cc_pair_id)

    lock: RedisLock = r.lock(
        DANSWER_REDIS_FUNCTION_LOCK_PREFIX + "try_generate_permissions_sync_tasks",
        timeout=LOCK_TIMEOUT,
    )

    acquired = lock.acquire(blocking_timeout=LOCK_TIMEOUT / 2)
    if not acquired:
        return None

    try:
        if redis_connector.permissions.fenced:
            return None

        if redis_connector.delete.fenced:
            return None

        if redis_connector.prune.fenced:
            return None

        redis_connector.permissions.generator_clear()
        redis_connector.permissions.taskset_clear()

        custom_task_id = f"{redis_connector.permissions.generator_task_key}_{uuid4()}"

        # create before setting fence to avoid race condition where the monitoring
        # task updates the sync record before it is created
        try:
            with get_session_with_tenant(tenant_id) as db_session:
                insert_sync_record(
                    db_session=db_session,
                    entity_id=cc_pair_id,
                    sync_type=SyncType.EXTERNAL_PERMISSIONS,
                )
        except Exception:
            task_logger.exception("insert_sync_record exceptioned.")

        # set a basic fence to start
        redis_connector.permissions.set_active()
        payload = RedisConnectorPermissionSyncPayload(
            id=make_short_id(),
            submitted=datetime.now(timezone.utc),
            started=None,
            celery_task_id=None,
        )
        redis_connector.permissions.set_fence(payload)

        result = app.send_task(
            OnyxCeleryTask.CONNECTOR_PERMISSION_SYNC_GENERATOR_TASK,
            kwargs=dict(
                cc_pair_id=cc_pair_id,
                tenant_id=tenant_id,
            ),
            queue=OnyxCeleryQueues.CONNECTOR_DOC_PERMISSIONS_SYNC,
            task_id=custom_task_id,
            priority=OnyxCeleryPriority.HIGH,
        )

        # fill in the celery task id
        payload.celery_task_id = result.id
        redis_connector.permissions.set_fence(payload)

        payload_id = payload.id
    except Exception:
        task_logger.exception(f"Unexpected exception: cc_pair={cc_pair_id}")
        return None
    finally:
        if lock.owned():
            lock.release()

    return payload_id


@shared_task(
    name=OnyxCeleryTask.CONNECTOR_PERMISSION_SYNC_GENERATOR_TASK,
    acks_late=False,
    soft_time_limit=JOB_TIMEOUT,
    track_started=True,
    trail=False,
    bind=True,
)
def connector_permission_sync_generator_task(
    self: Task,
    cc_pair_id: int,
    tenant_id: str | None,
) -> None:
    """
    Permission sync task that handles document permission syncing for a given connector credential pair
    This task assumes that the task has already been properly fenced
    """

    payload_id: str | None = None

    LoggerContextVars.reset()

    doc_permission_sync_ctx_dict = doc_permission_sync_ctx.get()
    doc_permission_sync_ctx_dict["cc_pair_id"] = cc_pair_id
    doc_permission_sync_ctx_dict["request_id"] = self.request.id
    doc_permission_sync_ctx.set(doc_permission_sync_ctx_dict)

    redis_connector = RedisConnector(tenant_id, cc_pair_id)

    r = get_redis_client(tenant_id=tenant_id)

    # this wait is needed to avoid a race condition where
    # the primary worker sends the task and it is immediately executed
    # before the primary worker can finalize the fence
    start = time.monotonic()
    while True:
        if time.monotonic() - start > CELERY_TASK_WAIT_FOR_FENCE_TIMEOUT:
            raise ValueError(
                f"connector_permission_sync_generator_task - timed out waiting for fence to be ready: "
                f"fence={redis_connector.permissions.fence_key}"
            )

        if not redis_connector.permissions.fenced:  # The fence must exist
            raise ValueError(
                f"connector_permission_sync_generator_task - fence not found: "
                f"fence={redis_connector.permissions.fence_key}"
            )

        payload = redis_connector.permissions.payload  # The payload must exist
        if not payload:
            raise ValueError(
                "connector_permission_sync_generator_task: payload invalid or not found"
            )

        if payload.celery_task_id is None:
            logger.info(
                f"connector_permission_sync_generator_task - Waiting for fence: "
                f"fence={redis_connector.permissions.fence_key}"
            )
            sleep(1)
            continue

        payload_id = payload.id

        logger.info(
            f"connector_permission_sync_generator_task - Fence found, continuing...: "
            f"fence={redis_connector.permissions.fence_key} "
            f"payload_id={payload.id}"
        )
        break

    lock: RedisLock = r.lock(
        OnyxRedisLocks.CONNECTOR_DOC_PERMISSIONS_SYNC_LOCK_PREFIX
        + f"_{redis_connector.id}",
        timeout=CELERY_PERMISSIONS_SYNC_LOCK_TIMEOUT,
    )

    acquired = lock.acquire(blocking=False)
    if not acquired:
        task_logger.warning(
            f"Permission sync task already running, exiting...: cc_pair={cc_pair_id}"
        )
        return None

    try:
        with get_session_with_tenant(tenant_id) as db_session:
            cc_pair = get_connector_credential_pair_from_id(
                db_session=db_session,
                cc_pair_id=cc_pair_id,
            )
            if cc_pair is None:
                raise ValueError(
                    f"No connector credential pair found for id: {cc_pair_id}"
                )

            source_type = cc_pair.connector.source

            doc_sync_func = DOC_PERMISSIONS_FUNC_MAP.get(source_type)
            if doc_sync_func is None:
                if source_type in DOC_SOURCE_TO_CHUNK_CENSORING_FUNCTION:
                    return None
                raise ValueError(
                    f"No doc sync func found for {source_type} with cc_pair={cc_pair_id}"
                )

            logger.info(f"Syncing docs for {source_type} with cc_pair={cc_pair_id}")

            payload = redis_connector.permissions.payload
            if not payload:
                raise ValueError(f"No fence payload found: cc_pair={cc_pair_id}")

            new_payload = RedisConnectorPermissionSyncPayload(
                id=payload.id,
                submitted=payload.submitted,
                started=datetime.now(timezone.utc),
                celery_task_id=payload.celery_task_id,
            )
            redis_connector.permissions.set_fence(new_payload)

            callback = PermissionSyncCallback(redis_connector, lock, r)
            document_external_accesses: list[DocExternalAccess] = doc_sync_func(
                cc_pair, callback
            )

            task_logger.info(
                f"RedisConnector.permissions.generate_tasks starting. cc_pair={cc_pair_id}"
            )
            tasks_generated = redis_connector.permissions.generate_tasks(
                celery_app=self.app,
                lock=lock,
                new_permissions=document_external_accesses,
                source_string=source_type,
                connector_id=cc_pair.connector.id,
                credential_id=cc_pair.credential.id,
            )
            if tasks_generated is None:
                return None

            task_logger.info(
                f"RedisConnector.permissions.generate_tasks finished. "
                f"cc_pair={cc_pair_id} tasks_generated={tasks_generated}"
            )

            redis_connector.permissions.generator_complete = tasks_generated

    except Exception as e:
        task_logger.exception(
            f"Permission sync exceptioned: cc_pair={cc_pair_id} payload_id={payload_id}"
        )

        redis_connector.permissions.generator_clear()
        redis_connector.permissions.taskset_clear()
        redis_connector.permissions.set_fence(None)
        raise e
    finally:
        if lock.owned():
            lock.release()

    task_logger.info(
        f"Permission sync finished: cc_pair={cc_pair_id} payload_id={payload.id}"
    )


@shared_task(
    name=OnyxCeleryTask.UPDATE_EXTERNAL_DOCUMENT_PERMISSIONS_TASK,
    soft_time_limit=LIGHT_SOFT_TIME_LIMIT,
    time_limit=LIGHT_TIME_LIMIT,
    max_retries=DOCUMENT_PERMISSIONS_UPDATE_MAX_RETRIES,
    bind=True,
)
def update_external_document_permissions_task(
    self: Task,
    tenant_id: str | None,
    serialized_doc_external_access: dict,
    source_string: str,
    connector_id: int,
    credential_id: int,
) -> bool:
    start = time.monotonic()

    document_external_access = DocExternalAccess.from_dict(
        serialized_doc_external_access
    )
    doc_id = document_external_access.doc_id
    external_access = document_external_access.external_access
    try:
        with get_session_with_tenant(tenant_id) as db_session:
            # Add the users to the DB if they don't exist
            batch_add_ext_perm_user_if_not_exists(
                db_session=db_session,
                emails=list(external_access.external_user_emails),
            )
            # Then we upsert the document's external permissions in postgres
            created_new_doc = upsert_document_external_perms(
                db_session=db_session,
                doc_id=doc_id,
                external_access=external_access,
                source_type=DocumentSource(source_string),
            )

            if created_new_doc:
                # If a new document was created, we associate it with the cc_pair
                upsert_document_by_connector_credential_pair(
                    db_session=db_session,
                    connector_id=connector_id,
                    credential_id=credential_id,
                    document_ids=[doc_id],
                )

            elapsed = time.monotonic() - start
            task_logger.info(
                f"connector_id={connector_id} "
                f"doc={doc_id} "
                f"action=update_permissions "
                f"elapsed={elapsed:.2f}"
            )
    except Exception:
        task_logger.exception(
            f"Exception in update_external_document_permissions_task: "
            f"connector_id={connector_id} "
            f"doc_id={doc_id}"
        )
        return False

    return True


def validate_permission_sync_fences(
    tenant_id: str | None,
    r: Redis,
    r_replica: Redis,
    r_celery: Redis,
    lock_beat: RedisLock,
) -> None:
    # building lookup table can be expensive, so we won't bother
    # validating until the queue is small
    PERMISSION_SYNC_VALIDATION_MAX_QUEUE_LEN = 1024

    queue_len = celery_get_queue_length(
        OnyxCeleryQueues.DOC_PERMISSIONS_UPSERT, r_celery
    )
    if queue_len > PERMISSION_SYNC_VALIDATION_MAX_QUEUE_LEN:
        return

    queued_upsert_tasks = celery_get_queued_task_ids(
        OnyxCeleryQueues.DOC_PERMISSIONS_UPSERT, r_celery
    )
    reserved_generator_tasks = celery_get_unacked_task_ids(
        OnyxCeleryQueues.CONNECTOR_DOC_PERMISSIONS_SYNC, r_celery
    )

    # validate all existing permission sync jobs
    lock_beat.reacquire()
    keys = cast(set[Any], r_replica.smembers(OnyxRedisConstants.ACTIVE_FENCES))
    for key in keys:
        key_bytes = cast(bytes, key)
        key_str = key_bytes.decode("utf-8")
        if not key_str.startswith(RedisConnectorPermissionSync.FENCE_PREFIX):
            continue

        validate_permission_sync_fence(
            tenant_id,
            key_bytes,
            queued_upsert_tasks,
            reserved_generator_tasks,
            r,
            r_celery,
        )

        lock_beat.reacquire()

    return


def validate_permission_sync_fence(
    tenant_id: str | None,
    key_bytes: bytes,
    queued_tasks: set[str],
    reserved_tasks: set[str],
    r: Redis,
    r_celery: Redis,
) -> None:
    """Checks for the error condition where an indexing fence is set but the associated celery tasks don't exist.
    This can happen if the indexing worker hard crashes or is terminated.
    Being in this bad state means the fence will never clear without help, so this function
    gives the help.

    How this works:
    1. This function renews the active signal with a 5 minute TTL under the following conditions
    1.2. When the task is seen in the redis queue
    1.3. When the task is seen in the reserved / prefetched list

    2. Externally, the active signal is renewed when:
    2.1. The fence is created
    2.2. The indexing watchdog checks the spawned task.

    3. The TTL allows us to get through the transitions on fence startup
    and when the task starts executing.

    More TTL clarification: it is seemingly impossible to exactly query Celery for
    whether a task is in the queue or currently executing.
    1. An unknown task id is always returned as state PENDING.
    2. Redis can be inspected for the task id, but the task id is gone between the time a worker receives the task
    and the time it actually starts on the worker.

    queued_tasks: the celery queue of lightweight permission sync tasks
    reserved_tasks: prefetched tasks for sync task generator
    """
    # if the fence doesn't exist, there's nothing to do
    fence_key = key_bytes.decode("utf-8")
    cc_pair_id_str = RedisConnector.get_id_from_fence_key(fence_key)
    if cc_pair_id_str is None:
        task_logger.warning(
            f"validate_permission_sync_fence - could not parse id from {fence_key}"
        )
        return

    cc_pair_id = int(cc_pair_id_str)
    # parse out metadata and initialize the helper class with it
    redis_connector = RedisConnector(tenant_id, int(cc_pair_id))

    # check to see if the fence/payload exists
    if not redis_connector.permissions.fenced:
        return

    # in the cloud, the payload format may have changed ...
    # it's a little sloppy, but just reset the fence for now if that happens
    # TODO: add intentional cleanup/abort logic
    try:
        payload = redis_connector.permissions.payload
    except ValidationError:
        task_logger.exception(
            "validate_permission_sync_fence - "
            "Resetting fence because fence schema is out of date: "
            f"cc_pair={cc_pair_id} "
            f"fence={fence_key}"
        )

        redis_connector.permissions.reset()
        return

    if not payload:
        return

    if not payload.celery_task_id:
        return

    # OK, there's actually something for us to validate

    # either the generator task must be in flight or its subtasks must be
    found = celery_find_task(
        payload.celery_task_id,
        OnyxCeleryQueues.CONNECTOR_DOC_PERMISSIONS_SYNC,
        r_celery,
    )
    if found:
        # the celery task exists in the redis queue
        redis_connector.permissions.set_active()
        return

    if payload.celery_task_id in reserved_tasks:
        # the celery task was prefetched and is reserved within a worker
        redis_connector.permissions.set_active()
        return

    # look up every task in the current taskset in the celery queue
    # every entry in the taskset should have an associated entry in the celery task queue
    # because we get the celery tasks first, the entries in our own permissions taskset
    # should be roughly a subset of the tasks in celery

    # this check isn't very exact, but should be sufficient over a period of time
    # A single successful check over some number of attempts is sufficient.

    # TODO: if the number of tasks in celery is much lower than than the taskset length
    # we might be able to shortcut the lookup since by definition some of the tasks
    # must not exist in celery.

    tasks_scanned = 0
    tasks_not_in_celery = 0  # a non-zero number after completing our check is bad

    for member in r.sscan_iter(redis_connector.permissions.taskset_key):
        tasks_scanned += 1

        member_bytes = cast(bytes, member)
        member_str = member_bytes.decode("utf-8")
        if member_str in queued_tasks:
            continue

        if member_str in reserved_tasks:
            continue

        tasks_not_in_celery += 1

    task_logger.info(
        "validate_permission_sync_fence task check: "
        f"tasks_scanned={tasks_scanned} tasks_not_in_celery={tasks_not_in_celery}"
    )

    # we're active if there are still tasks to run and those tasks all exist in celery
    if tasks_scanned > 0 and tasks_not_in_celery == 0:
        redis_connector.permissions.set_active()
        return

    # we may want to enable this check if using the active task list somehow isn't good enough
    # if redis_connector_index.generator_locked():
    #     logger.info(f"{payload.celery_task_id} is currently executing.")

    # if we get here, we didn't find any direct indication that the associated celery tasks exist,
    # but they still might be there due to gaps in our ability to check states during transitions
    # Checking the active signal safeguards us against these transition periods
    # (which has a duration that allows us to bridge those gaps)
    if redis_connector.permissions.active():
        return

    # celery tasks don't exist and the active signal has expired, possibly due to a crash. Clean it up.
    task_logger.warning(
        "validate_permission_sync_fence - "
        "Resetting fence because no associated celery tasks were found: "
        f"cc_pair={cc_pair_id} "
        f"fence={fence_key} "
        f"payload_id={payload.id}"
    )

    redis_connector.permissions.reset()
    return


class PermissionSyncCallback(IndexingHeartbeatInterface):
    PARENT_CHECK_INTERVAL = 60

    def __init__(
        self,
        redis_connector: RedisConnector,
        redis_lock: RedisLock,
        redis_client: Redis,
    ):
        super().__init__()
        self.redis_connector: RedisConnector = redis_connector
        self.redis_lock: RedisLock = redis_lock
        self.redis_client = redis_client

        self.started: datetime = datetime.now(timezone.utc)
        self.redis_lock.reacquire()

        self.last_tag: str = "PermissionSyncCallback.__init__"
        self.last_lock_reacquire: datetime = datetime.now(timezone.utc)
        self.last_lock_monotonic = time.monotonic()

    def should_stop(self) -> bool:
        if self.redis_connector.stop.fenced:
            return True

        return False

    def progress(self, tag: str, amount: int) -> None:
        try:
            self.redis_connector.permissions.set_active()

            current_time = time.monotonic()
            if current_time - self.last_lock_monotonic >= (
                CELERY_GENERIC_BEAT_LOCK_TIMEOUT / 4
            ):
                self.redis_lock.reacquire()
                self.last_lock_reacquire = datetime.now(timezone.utc)
                self.last_lock_monotonic = time.monotonic()

            self.last_tag = tag
        except LockError:
            logger.exception(
                f"PermissionSyncCallback - lock.reacquire exceptioned: "
                f"lock_timeout={self.redis_lock.timeout} "
                f"start={self.started} "
                f"last_tag={self.last_tag} "
                f"last_reacquired={self.last_lock_reacquire} "
                f"now={datetime.now(timezone.utc)}"
            )

            redis_lock_dump(self.redis_lock, self.redis_client)
            raise


"""Monitoring CCPair permissions utils, called in monitor_vespa_sync"""


def monitor_ccpair_permissions_taskset(
    tenant_id: str | None, key_bytes: bytes, r: Redis, db_session: Session
) -> None:
    fence_key = key_bytes.decode("utf-8")
    cc_pair_id_str = RedisConnector.get_id_from_fence_key(fence_key)
    if cc_pair_id_str is None:
        task_logger.warning(
            f"monitor_ccpair_permissions_taskset: could not parse cc_pair_id from {fence_key}"
        )
        return

    cc_pair_id = int(cc_pair_id_str)

    redis_connector = RedisConnector(tenant_id, cc_pair_id)
    if not redis_connector.permissions.fenced:
        return

    initial = redis_connector.permissions.generator_complete
    if initial is None:
        return

    try:
        payload = redis_connector.permissions.payload
    except ValidationError:
        task_logger.exception(
            "Permissions sync payload failed to validate. "
            "Schema may have been updated."
        )
        return

    if not payload:
        return

    remaining = redis_connector.permissions.get_remaining()
    task_logger.info(
        f"Permissions sync progress: "
        f"cc_pair={cc_pair_id} "
        f"id={payload.id} "
        f"remaining={remaining} "
        f"initial={initial}"
    )
    if remaining > 0:
        return

    mark_cc_pair_as_permissions_synced(db_session, int(cc_pair_id), payload.started)
    task_logger.info(
        f"Permissions sync finished: "
        f"cc_pair={cc_pair_id} "
        f"id={payload.id} "
        f"num_synced={initial}"
    )

    update_sync_record_status(
        db_session=db_session,
        entity_id=cc_pair_id,
        sync_type=SyncType.EXTERNAL_PERMISSIONS,
        sync_status=SyncStatus.SUCCESS,
        num_docs_synced=initial,
    )

    redis_connector.permissions.reset()
