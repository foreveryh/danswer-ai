from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from celery import Celery
from celery import shared_task
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from redis import Redis

from danswer.access.models import DocExternalAccess
from danswer.background.celery.apps.app_base import task_logger
from danswer.configs.app_configs import JOB_TIMEOUT
from danswer.configs.constants import CELERY_PERMISSIONS_SYNC_LOCK_TIMEOUT
from danswer.configs.constants import CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT
from danswer.configs.constants import DANSWER_REDIS_FUNCTION_LOCK_PREFIX
from danswer.configs.constants import DanswerCeleryPriority
from danswer.configs.constants import DanswerCeleryQueues
from danswer.configs.constants import DanswerRedisLocks
from danswer.configs.constants import DocumentSource
from danswer.db.connector_credential_pair import get_connector_credential_pair_from_id
from danswer.db.engine import get_session_with_tenant
from danswer.db.enums import AccessType
from danswer.db.enums import ConnectorCredentialPairStatus
from danswer.db.models import ConnectorCredentialPair
from danswer.db.users import batch_add_non_web_user_if_not_exists
from danswer.redis.redis_connector import RedisConnector
from danswer.redis.redis_connector_doc_perm_sync import (
    RedisConnectorPermissionSyncData,
)
from danswer.redis.redis_pool import get_redis_client
from danswer.utils.logger import doc_permission_sync_ctx
from danswer.utils.logger import setup_logger
from ee.danswer.db.connector_credential_pair import get_all_auto_sync_cc_pairs
from ee.danswer.db.document import upsert_document_external_perms
from ee.danswer.external_permissions.sync_params import DOC_PERMISSION_SYNC_PERIODS
from ee.danswer.external_permissions.sync_params import DOC_PERMISSIONS_FUNC_MAP

logger = setup_logger()


DOCUMENT_PERMISSIONS_UPDATE_MAX_RETRIES = 3


# 5 seconds more than RetryDocumentIndex STOP_AFTER+MAX_WAIT
LIGHT_SOFT_TIME_LIMIT = 105
LIGHT_TIME_LIMIT = LIGHT_SOFT_TIME_LIMIT + 15


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
    name="check_for_doc_permissions_sync",
    soft_time_limit=JOB_TIMEOUT,
    bind=True,
)
def check_for_doc_permissions_sync(self: Task, *, tenant_id: str | None) -> None:
    r = get_redis_client(tenant_id=tenant_id)

    lock_beat = r.lock(
        DanswerRedisLocks.CHECK_CONNECTOR_DOC_PERMISSIONS_SYNC_BEAT_LOCK,
        timeout=CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT,
    )

    try:
        # these tasks should never overlap
        if not lock_beat.acquire(blocking=False):
            return

        # get all cc pairs that need to be synced
        cc_pair_ids_to_sync: list[int] = []
        with get_session_with_tenant(tenant_id) as db_session:
            cc_pairs = get_all_auto_sync_cc_pairs(db_session)

            for cc_pair in cc_pairs:
                if _is_external_doc_permissions_sync_due(cc_pair):
                    cc_pair_ids_to_sync.append(cc_pair.id)

        for cc_pair_id in cc_pair_ids_to_sync:
            tasks_created = try_creating_permissions_sync_task(
                self.app, cc_pair_id, r, tenant_id
            )
            if not tasks_created:
                continue

            task_logger.info(f"Doc permissions sync queued: cc_pair={cc_pair_id}")
    except SoftTimeLimitExceeded:
        task_logger.info(
            "Soft time limit exceeded, task is being terminated gracefully."
        )
    except Exception:
        task_logger.exception(f"Unexpected exception: tenant={tenant_id}")
    finally:
        if lock_beat.owned():
            lock_beat.release()


def try_creating_permissions_sync_task(
    app: Celery,
    cc_pair_id: int,
    r: Redis,
    tenant_id: str | None,
) -> int | None:
    """Returns an int if syncing is needed. The int represents the number of sync tasks generated.
    Returns None if no syncing is required."""
    redis_connector = RedisConnector(tenant_id, cc_pair_id)

    LOCK_TIMEOUT = 30

    lock = r.lock(
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

        app.send_task(
            "connector_permission_sync_generator_task",
            kwargs=dict(
                cc_pair_id=cc_pair_id,
                tenant_id=tenant_id,
            ),
            queue=DanswerCeleryQueues.CONNECTOR_DOC_PERMISSIONS_SYNC,
            task_id=custom_task_id,
            priority=DanswerCeleryPriority.HIGH,
        )

        # set a basic fence to start
        payload = RedisConnectorPermissionSyncData(
            started=None,
        )

        redis_connector.permissions.set_fence(payload)
    except Exception:
        task_logger.exception(f"Unexpected exception: cc_pair={cc_pair_id}")
        return None
    finally:
        if lock.owned():
            lock.release()

    return 1


@shared_task(
    name="connector_permission_sync_generator_task",
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

    doc_permission_sync_ctx_dict = doc_permission_sync_ctx.get()
    doc_permission_sync_ctx_dict["cc_pair_id"] = cc_pair_id
    doc_permission_sync_ctx_dict["request_id"] = self.request.id
    doc_permission_sync_ctx.set(doc_permission_sync_ctx_dict)

    redis_connector = RedisConnector(tenant_id, cc_pair_id)

    r = get_redis_client(tenant_id=tenant_id)

    lock = r.lock(
        DanswerRedisLocks.CONNECTOR_DOC_PERMISSIONS_SYNC_LOCK_PREFIX
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
            cc_pair = get_connector_credential_pair_from_id(cc_pair_id, db_session)
            if cc_pair is None:
                raise ValueError(
                    f"No connector credential pair found for id: {cc_pair_id}"
                )

            source_type = cc_pair.connector.source

            doc_sync_func = DOC_PERMISSIONS_FUNC_MAP.get(source_type)
            if doc_sync_func is None:
                raise ValueError(f"No doc sync func found for {source_type}")

            logger.info(f"Syncing docs for {source_type}")

            payload = RedisConnectorPermissionSyncData(
                started=datetime.now(timezone.utc),
            )
            redis_connector.permissions.set_fence(payload)

            document_external_accesses: list[DocExternalAccess] = doc_sync_func(cc_pair)

            task_logger.info(
                f"RedisConnector.permissions.generate_tasks starting. cc_pair={cc_pair_id}"
            )
            tasks_generated = redis_connector.permissions.generate_tasks(
                self.app, lock, document_external_accesses, source_type
            )
            if tasks_generated is None:
                return None

            task_logger.info(
                f"RedisConnector.permissions.generate_tasks finished. "
                f"cc_pair={cc_pair_id} tasks_generated={tasks_generated}"
            )

            redis_connector.permissions.generator_complete = tasks_generated

    except Exception as e:
        task_logger.exception(f"Failed to run permission sync: cc_pair={cc_pair_id}")

        redis_connector.permissions.generator_clear()
        redis_connector.permissions.taskset_clear()
        redis_connector.permissions.set_fence(None)
        raise e
    finally:
        if lock.owned():
            lock.release()


@shared_task(
    name="update_external_document_permissions_task",
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
) -> bool:
    document_external_access = DocExternalAccess.from_dict(
        serialized_doc_external_access
    )
    doc_id = document_external_access.doc_id
    external_access = document_external_access.external_access
    try:
        with get_session_with_tenant(tenant_id) as db_session:
            # Then we build the update requests to update vespa
            batch_add_non_web_user_if_not_exists(
                db_session=db_session,
                emails=list(external_access.external_user_emails),
            )
            upsert_document_external_perms(
                db_session=db_session,
                doc_id=doc_id,
                external_access=external_access,
                source_type=DocumentSource(source_string),
            )

            logger.debug(
                f"Successfully synced postgres document permissions for {doc_id}"
            )
        return True
    except Exception:
        logger.exception("Error Syncing Document Permissions")
        return False
