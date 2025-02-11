from onyx.configs.constants import KV_SETTINGS_KEY
from onyx.configs.constants import OnyxRedisLocks
from onyx.key_value_store.factory import get_kv_store
from onyx.redis.redis_pool import get_redis_client
from onyx.server.settings.models import Settings
from onyx.utils.logger import setup_logger
from shared_configs.configs import MULTI_TENANT
from shared_configs.contextvars import CURRENT_TENANT_ID_CONTEXTVAR

logger = setup_logger()


def load_settings() -> Settings:
    kv_store = get_kv_store()
    try:
        stored_settings = kv_store.load(KV_SETTINGS_KEY)
        settings = (
            Settings.model_validate(stored_settings) if stored_settings else Settings()
        )
    except Exception as e:
        logger.error(f"Error loading settings from KV store: {str(e)}")
        settings = Settings()

    tenant_id = CURRENT_TENANT_ID_CONTEXTVAR.get() if MULTI_TENANT else None
    redis_client = get_redis_client(tenant_id=tenant_id)

    try:
        value = redis_client.get(OnyxRedisLocks.ANONYMOUS_USER_ENABLED)
        if value is not None:
            assert isinstance(value, bytes)
            anonymous_user_enabled = int(value.decode("utf-8")) == 1
        else:
            # Default to False
            anonymous_user_enabled = False
            # Optionally store the default back to Redis
            redis_client.set(OnyxRedisLocks.ANONYMOUS_USER_ENABLED, "0")
    except Exception as e:
        # Log the error and reset to default
        logger.error(f"Error loading anonymous user setting from Redis: {str(e)}")
        anonymous_user_enabled = False

    settings.anonymous_user_enabled = anonymous_user_enabled
    return settings


def store_settings(settings: Settings) -> None:
    tenant_id = CURRENT_TENANT_ID_CONTEXTVAR.get() if MULTI_TENANT else None
    redis_client = get_redis_client(tenant_id=tenant_id)

    if settings.anonymous_user_enabled is not None:
        redis_client.set(
            OnyxRedisLocks.ANONYMOUS_USER_ENABLED,
            "1" if settings.anonymous_user_enabled else "0",
        )

    get_kv_store().store(KV_SETTINGS_KEY, settings.model_dump())
