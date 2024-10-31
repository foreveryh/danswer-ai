# These are helper objects for tracking the keys we need to write in redis
import time
from abc import ABC
from abc import abstractmethod
from typing import cast
from uuid import uuid4

import redis
from celery import Celery
from redis import Redis
from sqlalchemy.orm import Session

from danswer.background.celery.configs.base import CELERY_SEPARATOR
from danswer.configs.constants import CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT
from danswer.configs.constants import DanswerCeleryPriority
from danswer.configs.constants import DanswerCeleryQueues
from danswer.db.connector_credential_pair import get_connector_credential_pair_from_id
from danswer.db.document import construct_document_select_for_connector_credential_pair
from danswer.db.document import (
    construct_document_select_for_connector_credential_pair_by_needs_sync,
)
from danswer.db.document_set import construct_document_select_by_docset
from danswer.utils.variable_functionality import fetch_versioned_implementation
from danswer.utils.variable_functionality import global_version


class RedisObjectHelper(ABC):
    PREFIX = "base"
    FENCE_PREFIX = PREFIX + "_fence"
    TASKSET_PREFIX = PREFIX + "_taskset"

    def __init__(self, id: str):
        self._id: str = id

    @property
    def task_id_prefix(self) -> str:
        return f"{self.PREFIX}_{self._id}"

    @property
    def fence_key(self) -> str:
        # example: documentset_fence_1
        return f"{self.FENCE_PREFIX}_{self._id}"

    @property
    def taskset_key(self) -> str:
        # example: documentset_taskset_1
        return f"{self.TASKSET_PREFIX}_{self._id}"

    @staticmethod
    def get_id_from_fence_key(key: str) -> str | None:
        """
        Extracts the object ID from a fence key in the format `PREFIX_fence_X`.

        Args:
            key (str): The fence key string.

        Returns:
            Optional[int]: The extracted ID if the key is in the correct format, otherwise None.
        """
        parts = key.split("_")
        if len(parts) != 3:
            return None

        object_id = parts[2]
        return object_id

    @staticmethod
    def get_id_from_task_id(task_id: str) -> str | None:
        """
        Extracts the object ID from a task ID string.

        This method assumes the task ID is formatted as `prefix_objectid_suffix`, where:
        - `prefix` is an arbitrary string (e.g., the name of the task or entity),
        - `objectid` is the ID you want to extract,
        - `suffix` is another arbitrary string (e.g., a UUID).

        Example:
            If the input `task_id` is `documentset_1_cbfdc96a-80ca-4312-a242-0bb68da3c1dc`,
            this method will return the string `"1"`.

        Args:
            task_id (str): The task ID string from which to extract the object ID.

        Returns:
            str | None: The extracted object ID if the task ID is in the correct format, otherwise None.
        """
        # example: task_id=documentset_1_cbfdc96a-80ca-4312-a242-0bb68da3c1dc
        parts = task_id.split("_")
        if len(parts) != 3:
            return None

        object_id = parts[1]
        return object_id

    @abstractmethod
    def generate_tasks(
        self,
        celery_app: Celery,
        db_session: Session,
        redis_client: Redis,
        lock: redis.lock.Lock,
        tenant_id: str | None,
    ) -> int | None:
        pass


class RedisDocumentSet(RedisObjectHelper):
    PREFIX = "documentset"
    FENCE_PREFIX = PREFIX + "_fence"
    TASKSET_PREFIX = PREFIX + "_taskset"

    def __init__(self, id: int) -> None:
        super().__init__(str(id))

    def generate_tasks(
        self,
        celery_app: Celery,
        db_session: Session,
        redis_client: Redis,
        lock: redis.lock.Lock,
        tenant_id: str | None,
    ) -> int | None:
        last_lock_time = time.monotonic()

        async_results = []
        stmt = construct_document_select_by_docset(int(self._id), current_only=False)
        for doc in db_session.scalars(stmt).yield_per(1):
            current_time = time.monotonic()
            if current_time - last_lock_time >= (
                CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT / 4
            ):
                lock.reacquire()
                last_lock_time = current_time

            # celery's default task id format is "dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # the key for the result is "celery-task-meta-dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # we prefix the task id so it's easier to keep track of who created the task
            # aka "documentset_1_6dd32ded3-00aa-4884-8b21-42f8332e7fac"
            custom_task_id = f"{self.task_id_prefix}_{uuid4()}"

            # add to the set BEFORE creating the task.
            redis_client.sadd(self.taskset_key, custom_task_id)

            result = celery_app.send_task(
                "vespa_metadata_sync_task",
                kwargs=dict(document_id=doc.id, tenant_id=tenant_id),
                queue=DanswerCeleryQueues.VESPA_METADATA_SYNC,
                task_id=custom_task_id,
                priority=DanswerCeleryPriority.LOW,
            )

            async_results.append(result)

        return len(async_results)


class RedisUserGroup(RedisObjectHelper):
    PREFIX = "usergroup"
    FENCE_PREFIX = PREFIX + "_fence"
    TASKSET_PREFIX = PREFIX + "_taskset"

    def __init__(self, id: int) -> None:
        super().__init__(str(id))

    def generate_tasks(
        self,
        celery_app: Celery,
        db_session: Session,
        redis_client: Redis,
        lock: redis.lock.Lock,
        tenant_id: str | None,
    ) -> int | None:
        last_lock_time = time.monotonic()

        async_results = []

        if not global_version.is_ee_version():
            return 0

        try:
            construct_document_select_by_usergroup = fetch_versioned_implementation(
                "danswer.db.user_group",
                "construct_document_select_by_usergroup",
            )
        except ModuleNotFoundError:
            return 0

        stmt = construct_document_select_by_usergroup(int(self._id))
        for doc in db_session.scalars(stmt).yield_per(1):
            current_time = time.monotonic()
            if current_time - last_lock_time >= (
                CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT / 4
            ):
                lock.reacquire()
                last_lock_time = current_time

            # celery's default task id format is "dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # the key for the result is "celery-task-meta-dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # we prefix the task id so it's easier to keep track of who created the task
            # aka "documentset_1_6dd32ded3-00aa-4884-8b21-42f8332e7fac"
            custom_task_id = f"{self.task_id_prefix}_{uuid4()}"

            # add to the set BEFORE creating the task.
            redis_client.sadd(self.taskset_key, custom_task_id)

            result = celery_app.send_task(
                "vespa_metadata_sync_task",
                kwargs=dict(document_id=doc.id, tenant_id=tenant_id),
                queue=DanswerCeleryQueues.VESPA_METADATA_SYNC,
                task_id=custom_task_id,
                priority=DanswerCeleryPriority.LOW,
            )

            async_results.append(result)

        return len(async_results)


class RedisConnectorCredentialPair(RedisObjectHelper):
    """This class is used to scan documents by cc_pair in the db and collect them into
    a unified set for syncing.

    It differs from the other redis helpers in that the taskset used spans
    all connectors and is not per connector."""

    PREFIX = "connectorsync"
    FENCE_PREFIX = PREFIX + "_fence"
    TASKSET_PREFIX = PREFIX + "_taskset"

    def __init__(self, id: int) -> None:
        super().__init__(str(id))

    @classmethod
    def get_fence_key(cls) -> str:
        return RedisConnectorCredentialPair.FENCE_PREFIX

    @classmethod
    def get_taskset_key(cls) -> str:
        return RedisConnectorCredentialPair.TASKSET_PREFIX

    @property
    def taskset_key(self) -> str:
        """Notice that this is intentionally reusing the same taskset for all
        connector syncs"""
        # example: connector_taskset
        return f"{self.TASKSET_PREFIX}"

    def generate_tasks(
        self,
        celery_app: Celery,
        db_session: Session,
        redis_client: Redis,
        lock: redis.lock.Lock,
        tenant_id: str | None,
    ) -> int | None:
        last_lock_time = time.monotonic()

        async_results = []
        cc_pair = get_connector_credential_pair_from_id(int(self._id), db_session)
        if not cc_pair:
            return None

        stmt = construct_document_select_for_connector_credential_pair_by_needs_sync(
            cc_pair.connector_id, cc_pair.credential_id
        )
        for doc in db_session.scalars(stmt).yield_per(1):
            current_time = time.monotonic()
            if current_time - last_lock_time >= (
                CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT / 4
            ):
                lock.reacquire()
                last_lock_time = current_time

            # celery's default task id format is "dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # the key for the result is "celery-task-meta-dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # we prefix the task id so it's easier to keep track of who created the task
            # aka "documentset_1_6dd32ded3-00aa-4884-8b21-42f8332e7fac"
            custom_task_id = f"{self.task_id_prefix}_{uuid4()}"

            # add to the tracking taskset in redis BEFORE creating the celery task.
            # note that for the moment we are using a single taskset key, not differentiated by cc_pair id
            redis_client.sadd(
                RedisConnectorCredentialPair.get_taskset_key(), custom_task_id
            )

            # Priority on sync's triggered by new indexing should be medium
            result = celery_app.send_task(
                "vespa_metadata_sync_task",
                kwargs=dict(document_id=doc.id, tenant_id=tenant_id),
                queue=DanswerCeleryQueues.VESPA_METADATA_SYNC,
                task_id=custom_task_id,
                priority=DanswerCeleryPriority.MEDIUM,
            )

            async_results.append(result)

        return len(async_results)


class RedisConnectorDeletion(RedisObjectHelper):
    PREFIX = "connectordeletion"
    FENCE_PREFIX = PREFIX + "_fence"
    TASKSET_PREFIX = PREFIX + "_taskset"

    def __init__(self, id: int) -> None:
        super().__init__(str(id))

    def generate_tasks(
        self,
        celery_app: Celery,
        db_session: Session,
        redis_client: Redis,
        lock: redis.lock.Lock,
        tenant_id: str | None,
    ) -> int | None:
        """Returns None if the cc_pair doesn't exist.
        Otherwise, returns an int with the number of generated tasks."""
        last_lock_time = time.monotonic()

        async_results = []
        cc_pair = get_connector_credential_pair_from_id(int(self._id), db_session)
        if not cc_pair:
            return None

        stmt = construct_document_select_for_connector_credential_pair(
            cc_pair.connector_id, cc_pair.credential_id
        )
        for doc in db_session.scalars(stmt).yield_per(1):
            current_time = time.monotonic()
            if current_time - last_lock_time >= (
                CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT / 4
            ):
                lock.reacquire()
                last_lock_time = current_time

            # celery's default task id format is "dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # the actual redis key is "celery-task-meta-dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # we prefix the task id so it's easier to keep track of who created the task
            # aka "documentset_1_6dd32ded3-00aa-4884-8b21-42f8332e7fac"
            custom_task_id = f"{self.task_id_prefix}_{uuid4()}"

            # add to the tracking taskset in redis BEFORE creating the celery task.
            # note that for the moment we are using a single taskset key, not differentiated by cc_pair id
            redis_client.sadd(self.taskset_key, custom_task_id)

            # Priority on sync's triggered by new indexing should be medium
            result = celery_app.send_task(
                "document_by_cc_pair_cleanup_task",
                kwargs=dict(
                    document_id=doc.id,
                    connector_id=cc_pair.connector_id,
                    credential_id=cc_pair.credential_id,
                    tenant_id=tenant_id,
                ),
                queue=DanswerCeleryQueues.CONNECTOR_DELETION,
                task_id=custom_task_id,
                priority=DanswerCeleryPriority.MEDIUM,
            )

            async_results.append(result)

        return len(async_results)


class RedisConnectorPruning(RedisObjectHelper):
    """Celery will kick off a long running generator task to crawl the connector and
    find any missing docs, which will each then get a new cleanup task. The progress of
    those tasks will then be monitored to completion.

    Example rough happy path order:
    Check connectorpruning_fence_1
    Send generator task with id connectorpruning+generator_1_{uuid}

    generator runs connector with callbacks that increment connectorpruning_generator_progress_1
    generator creates many subtasks with id connectorpruning+sub_1_{uuid}
      in taskset connectorpruning_taskset_1
    on completion, generator sets connectorpruning_generator_complete_1

    celery postrun removes subtasks from taskset
    monitor beat task cleans up when taskset reaches 0 items
    """

    PREFIX = "connectorpruning"
    FENCE_PREFIX = PREFIX + "_fence"  # a fence for the entire pruning process
    GENERATOR_TASK_PREFIX = PREFIX + "+generator"

    TASKSET_PREFIX = PREFIX + "_taskset"  # stores a list of prune tasks id's
    SUBTASK_PREFIX = PREFIX + "+sub"

    GENERATOR_PROGRESS_PREFIX = (
        PREFIX + "_generator_progress"
    )  # a signal that contains generator progress
    GENERATOR_COMPLETE_PREFIX = (
        PREFIX + "_generator_complete"
    )  # a signal that the generator has finished

    def __init__(self, id: int) -> None:
        super().__init__(str(id))
        self.documents_to_prune: set[str] = set()

    @property
    def generator_task_id_prefix(self) -> str:
        return f"{self.GENERATOR_TASK_PREFIX}_{self._id}"

    @property
    def generator_progress_key(self) -> str:
        # example: connectorpruning_generator_progress_1
        return f"{self.GENERATOR_PROGRESS_PREFIX}_{self._id}"

    @property
    def generator_complete_key(self) -> str:
        # example: connectorpruning_generator_complete_1
        return f"{self.GENERATOR_COMPLETE_PREFIX}_{self._id}"

    @property
    def subtask_id_prefix(self) -> str:
        return f"{self.SUBTASK_PREFIX}_{self._id}"

    def generate_tasks(
        self,
        celery_app: Celery,
        db_session: Session,
        redis_client: Redis,
        lock: redis.lock.Lock | None,
        tenant_id: str | None,
    ) -> int | None:
        last_lock_time = time.monotonic()

        async_results = []
        cc_pair = get_connector_credential_pair_from_id(int(self._id), db_session)
        if not cc_pair:
            return None

        for doc_id in self.documents_to_prune:
            current_time = time.monotonic()
            if lock and current_time - last_lock_time >= (
                CELERY_VESPA_SYNC_BEAT_LOCK_TIMEOUT / 4
            ):
                lock.reacquire()
                last_lock_time = current_time

            # celery's default task id format is "dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # the actual redis key is "celery-task-meta-dd32ded3-00aa-4884-8b21-42f8332e7fac"
            # we prefix the task id so it's easier to keep track of who created the task
            # aka "documentset_1_6dd32ded3-00aa-4884-8b21-42f8332e7fac"
            custom_task_id = f"{self.subtask_id_prefix}_{uuid4()}"

            # add to the tracking taskset in redis BEFORE creating the celery task.
            # note that for the moment we are using a single taskset key, not differentiated by cc_pair id
            redis_client.sadd(self.taskset_key, custom_task_id)

            # Priority on sync's triggered by new indexing should be medium
            result = celery_app.send_task(
                "document_by_cc_pair_cleanup_task",
                kwargs=dict(
                    document_id=doc_id,
                    connector_id=cc_pair.connector_id,
                    credential_id=cc_pair.credential_id,
                    tenant_id=tenant_id,
                ),
                queue=DanswerCeleryQueues.CONNECTOR_DELETION,
                task_id=custom_task_id,
                priority=DanswerCeleryPriority.MEDIUM,
            )

            async_results.append(result)

        return len(async_results)

    def is_pruning(self, redis_client: Redis) -> bool:
        """A single example of a helper method being refactored into the redis helper"""
        if redis_client.exists(self.fence_key):
            return True

        return False


class RedisConnectorIndexing(RedisObjectHelper):
    """Celery will kick off a long running indexing task to crawl the connector and
    find any new or updated docs docs, which will each then get a new sync task or be
    indexed inline.

    ID should be a concatenation of cc_pair_id and search_setting_id, delimited by "/".
    e.g. "2/5"
    """

    PREFIX = "connectorindexing"
    FENCE_PREFIX = PREFIX + "_fence"  # a fence for the entire indexing process
    GENERATOR_TASK_PREFIX = PREFIX + "+generator"

    TASKSET_PREFIX = PREFIX + "_taskset"  # stores a list of prune tasks id's
    SUBTASK_PREFIX = PREFIX + "+sub"

    GENERATOR_LOCK_PREFIX = "da_lock:indexing"
    GENERATOR_PROGRESS_PREFIX = (
        PREFIX + "_generator_progress"
    )  # a signal that contains generator progress
    GENERATOR_COMPLETE_PREFIX = (
        PREFIX + "_generator_complete"
    )  # a signal that the generator has finished

    def __init__(self, cc_pair_id: int, search_settings_id: int) -> None:
        super().__init__(f"{cc_pair_id}/{search_settings_id}")

    @property
    def generator_lock_key(self) -> str:
        return f"{self.GENERATOR_LOCK_PREFIX}_{self._id}"

    @property
    def generator_task_id_prefix(self) -> str:
        return f"{self.GENERATOR_TASK_PREFIX}_{self._id}"

    @property
    def generator_progress_key(self) -> str:
        # example: connectorpruning_generator_progress_1
        return f"{self.GENERATOR_PROGRESS_PREFIX}_{self._id}"

    @property
    def generator_complete_key(self) -> str:
        # example: connectorpruning_generator_complete_1
        return f"{self.GENERATOR_COMPLETE_PREFIX}_{self._id}"

    @property
    def subtask_id_prefix(self) -> str:
        return f"{self.SUBTASK_PREFIX}_{self._id}"

    def generate_tasks(
        self,
        celery_app: Celery,
        db_session: Session,
        redis_client: Redis,
        lock: redis.lock.Lock | None,
        tenant_id: str | None,
    ) -> int | None:
        return None

    def is_indexing(self, redis_client: Redis) -> bool:
        """A single example of a helper method being refactored into the redis helper"""
        if redis_client.exists(self.fence_key):
            return True

        return False


class RedisConnectorStop(RedisObjectHelper):
    """Used to signal any running tasks for a connector to stop. We should refactor
    connector related redis helpers into a single class.
    """

    PREFIX = "connectorstop"
    FENCE_PREFIX = PREFIX + "_fence"  # a fence for the entire indexing process
    TASKSET_PREFIX = PREFIX + "_taskset"  # stores a list of prune tasks id's

    def __init__(self, id: int) -> None:
        super().__init__(str(id))

    def generate_tasks(
        self,
        celery_app: Celery,
        db_session: Session,
        redis_client: Redis,
        lock: redis.lock.Lock | None,
        tenant_id: str | None,
    ) -> int | None:
        return None


def celery_get_queue_length(queue: str, r: Redis) -> int:
    """This is a redis specific way to get the length of a celery queue.
    It is priority aware and knows how to count across the multiple redis lists
    used to implement task prioritization.
    This operation is not atomic."""
    total_length = 0
    for i in range(len(DanswerCeleryPriority)):
        queue_name = queue
        if i > 0:
            queue_name += CELERY_SEPARATOR
            queue_name += str(i)

        length = r.llen(queue_name)
        total_length += cast(int, length)

    return total_length
