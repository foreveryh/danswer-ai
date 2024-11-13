import time
from datetime import datetime
from typing import cast
from uuid import uuid4

import redis
from celery import Celery
from pydantic import BaseModel

from danswer.access.models import DocExternalAccess
from danswer.configs.constants import CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT
from danswer.configs.constants import DanswerCeleryPriority
from danswer.configs.constants import DanswerCeleryQueues


class RedisConnectorPermissionSyncData(BaseModel):
    started: datetime | None


class RedisConnectorPermissionSync:
    """Manages interactions with redis for doc permission sync tasks. Should only be accessed
    through RedisConnector."""

    PREFIX = "connectordocpermissionsync"

    FENCE_PREFIX = f"{PREFIX}_fence"

    # phase 1 - geneartor task and progress signals
    GENERATORTASK_PREFIX = f"{PREFIX}+generator"  # connectorpermissions+generator
    GENERATOR_PROGRESS_PREFIX = (
        PREFIX + "_generator_progress"
    )  # connectorpermissions_generator_progress
    GENERATOR_COMPLETE_PREFIX = (
        PREFIX + "_generator_complete"
    )  # connectorpermissions_generator_complete

    TASKSET_PREFIX = f"{PREFIX}_taskset"  # connectorpermissions_taskset
    SUBTASK_PREFIX = f"{PREFIX}+sub"  # connectorpermissions+sub

    def __init__(self, tenant_id: str | None, id: int, redis: redis.Redis) -> None:
        self.tenant_id: str | None = tenant_id
        self.id = id
        self.redis = redis

        self.fence_key: str = f"{self.FENCE_PREFIX}_{id}"
        self.generator_task_key = f"{self.GENERATORTASK_PREFIX}_{id}"
        self.generator_progress_key = f"{self.GENERATOR_PROGRESS_PREFIX}_{id}"
        self.generator_complete_key = f"{self.GENERATOR_COMPLETE_PREFIX}_{id}"

        self.taskset_key = f"{self.TASKSET_PREFIX}_{id}"

        self.subtask_prefix: str = f"{self.SUBTASK_PREFIX}_{id}"

    def taskset_clear(self) -> None:
        self.redis.delete(self.taskset_key)

    def generator_clear(self) -> None:
        self.redis.delete(self.generator_progress_key)
        self.redis.delete(self.generator_complete_key)

    def get_remaining(self) -> int:
        remaining = cast(int, self.redis.scard(self.taskset_key))
        return remaining

    def get_active_task_count(self) -> int:
        """Count of active permission sync tasks"""
        count = 0
        for _ in self.redis.scan_iter(RedisConnectorPermissionSync.FENCE_PREFIX + "*"):
            count += 1
        return count

    @property
    def fenced(self) -> bool:
        if self.redis.exists(self.fence_key):
            return True

        return False

    @property
    def payload(self) -> RedisConnectorPermissionSyncData | None:
        # read related data and evaluate/print task progress
        fence_bytes = cast(bytes, self.redis.get(self.fence_key))
        if fence_bytes is None:
            return None

        fence_str = fence_bytes.decode("utf-8")
        payload = RedisConnectorPermissionSyncData.model_validate_json(
            cast(str, fence_str)
        )

        return payload

    def set_fence(
        self,
        payload: RedisConnectorPermissionSyncData | None,
    ) -> None:
        if not payload:
            self.redis.delete(self.fence_key)
            return

        self.redis.set(self.fence_key, payload.model_dump_json())

    @property
    def generator_complete(self) -> int | None:
        """the fence payload is an int representing the starting number of
        permission sync tasks to be processed ... just after the generator completes."""
        fence_bytes = self.redis.get(self.generator_complete_key)
        if fence_bytes is None:
            return None

        if fence_bytes == b"None":
            return None

        fence_int = int(cast(bytes, fence_bytes).decode())
        return fence_int

    @generator_complete.setter
    def generator_complete(self, payload: int | None) -> None:
        """Set the payload to an int to set the fence, otherwise if None it will
        be deleted"""
        if payload is None:
            self.redis.delete(self.generator_complete_key)
            return

        self.redis.set(self.generator_complete_key, payload)

    def generate_tasks(
        self,
        celery_app: Celery,
        lock: redis.lock.Lock | None,
        new_permissions: list[DocExternalAccess],
        source_string: str,
    ) -> int | None:
        last_lock_time = time.monotonic()
        async_results = []

        # Create a task for each document permission sync
        for doc_perm in new_permissions:
            current_time = time.monotonic()
            if lock and current_time - last_lock_time >= (
                CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT / 4
            ):
                lock.reacquire()
                last_lock_time = current_time
            # Add task for document permissions sync
            custom_task_id = f"{self.subtask_prefix}_{uuid4()}"
            self.redis.sadd(self.taskset_key, custom_task_id)

            result = celery_app.send_task(
                "update_external_document_permissions_task",
                kwargs=dict(
                    tenant_id=self.tenant_id,
                    serialized_doc_external_access=doc_perm.to_dict(),
                    source_string=source_string,
                ),
                queue=DanswerCeleryQueues.DOC_PERMISSIONS_UPSERT,
                task_id=custom_task_id,
                priority=DanswerCeleryPriority.MEDIUM,
            )
            async_results.append(result)

        return len(async_results)

    @staticmethod
    def remove_from_taskset(id: int, task_id: str, r: redis.Redis) -> None:
        taskset_key = f"{RedisConnectorPermissionSync.TASKSET_PREFIX}_{id}"
        r.srem(taskset_key, task_id)
        return

    @staticmethod
    def reset_all(r: redis.Redis) -> None:
        """Deletes all redis values for all connectors"""
        for key in r.scan_iter(RedisConnectorPermissionSync.TASKSET_PREFIX + "*"):
            r.delete(key)

        for key in r.scan_iter(
            RedisConnectorPermissionSync.GENERATOR_COMPLETE_PREFIX + "*"
        ):
            r.delete(key)

        for key in r.scan_iter(
            RedisConnectorPermissionSync.GENERATOR_PROGRESS_PREFIX + "*"
        ):
            r.delete(key)

        for key in r.scan_iter(RedisConnectorPermissionSync.FENCE_PREFIX + "*"):
            r.delete(key)
