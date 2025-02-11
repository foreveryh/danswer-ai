import asyncio
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from contextvars import Token
from threading import Event
from types import FrameType
from typing import Any
from typing import cast
from typing import Dict
from typing import Set

from prometheus_client import Gauge
from prometheus_client import start_http_server
from redis.lock import Lock
from slack_sdk import WebClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from sqlalchemy.orm import Session

from onyx.chat.models import ThreadMessage
from onyx.configs.app_configs import DEV_MODE
from onyx.configs.app_configs import POD_NAME
from onyx.configs.app_configs import POD_NAMESPACE
from onyx.configs.constants import MessageType
from onyx.configs.constants import OnyxRedisLocks
from onyx.configs.onyxbot_configs import DANSWER_BOT_REPHRASE_MESSAGE
from onyx.configs.onyxbot_configs import DANSWER_BOT_RESPOND_EVERY_CHANNEL
from onyx.configs.onyxbot_configs import NOTIFY_SLACKBOT_NO_ANSWER
from onyx.connectors.slack.utils import expert_info_from_slack_id
from onyx.context.search.retrieval.search_runner import (
    download_nltk_data,
)
from onyx.db.engine import get_all_tenant_ids
from onyx.db.engine import get_session_with_tenant
from onyx.db.models import SlackBot
from onyx.db.search_settings import get_current_search_settings
from onyx.db.slack_bot import fetch_slack_bots
from onyx.key_value_store.interface import KvKeyNotFoundError
from onyx.natural_language_processing.search_nlp_models import EmbeddingModel
from onyx.natural_language_processing.search_nlp_models import warm_up_bi_encoder
from onyx.onyxbot.slack.config import get_slack_channel_config_for_bot_and_channel
from onyx.onyxbot.slack.config import MAX_TENANTS_PER_POD
from onyx.onyxbot.slack.config import TENANT_ACQUISITION_INTERVAL
from onyx.onyxbot.slack.config import TENANT_HEARTBEAT_EXPIRATION
from onyx.onyxbot.slack.config import TENANT_HEARTBEAT_INTERVAL
from onyx.onyxbot.slack.config import TENANT_LOCK_EXPIRATION
from onyx.onyxbot.slack.constants import DISLIKE_BLOCK_ACTION_ID
from onyx.onyxbot.slack.constants import FEEDBACK_DOC_BUTTON_BLOCK_ACTION_ID
from onyx.onyxbot.slack.constants import FOLLOWUP_BUTTON_ACTION_ID
from onyx.onyxbot.slack.constants import FOLLOWUP_BUTTON_RESOLVED_ACTION_ID
from onyx.onyxbot.slack.constants import GENERATE_ANSWER_BUTTON_ACTION_ID
from onyx.onyxbot.slack.constants import IMMEDIATE_RESOLVED_BUTTON_ACTION_ID
from onyx.onyxbot.slack.constants import LIKE_BLOCK_ACTION_ID
from onyx.onyxbot.slack.constants import VIEW_DOC_FEEDBACK_ID
from onyx.onyxbot.slack.handlers.handle_buttons import handle_doc_feedback_button
from onyx.onyxbot.slack.handlers.handle_buttons import handle_followup_button
from onyx.onyxbot.slack.handlers.handle_buttons import (
    handle_followup_resolved_button,
)
from onyx.onyxbot.slack.handlers.handle_buttons import (
    handle_generate_answer_button,
)
from onyx.onyxbot.slack.handlers.handle_buttons import handle_slack_feedback
from onyx.onyxbot.slack.handlers.handle_message import handle_message
from onyx.onyxbot.slack.handlers.handle_message import (
    remove_scheduled_feedback_reminder,
)
from onyx.onyxbot.slack.handlers.handle_message import schedule_feedback_reminder
from onyx.onyxbot.slack.models import SlackMessageInfo
from onyx.onyxbot.slack.utils import check_message_limit
from onyx.onyxbot.slack.utils import decompose_action_id
from onyx.onyxbot.slack.utils import get_channel_name_from_id
from onyx.onyxbot.slack.utils import get_onyx_bot_slack_bot_id
from onyx.onyxbot.slack.utils import read_slack_thread
from onyx.onyxbot.slack.utils import remove_onyx_bot_tag
from onyx.onyxbot.slack.utils import rephrase_slack_message
from onyx.onyxbot.slack.utils import respond_in_thread
from onyx.onyxbot.slack.utils import TenantSocketModeClient
from onyx.redis.redis_pool import get_redis_client
from onyx.server.manage.models import SlackBotTokens
from onyx.utils.logger import setup_logger
from onyx.utils.variable_functionality import set_is_ee_based_on_env_variable
from shared_configs.configs import DISALLOWED_SLACK_BOT_TENANT_LIST
from shared_configs.configs import MODEL_SERVER_HOST
from shared_configs.configs import MODEL_SERVER_PORT
from shared_configs.configs import POSTGRES_DEFAULT_SCHEMA
from shared_configs.configs import SLACK_CHANNEL_ID
from shared_configs.contextvars import CURRENT_TENANT_ID_CONTEXTVAR


logger = setup_logger()

# Prometheus metric for HPA
active_tenants_gauge = Gauge(
    "active_tenants",
    "Number of active tenants handled by this pod",
    ["namespace", "pod"],
)

# In rare cases, some users have been experiencing a massive amount of trivial messages coming through
# to the Slack Bot with trivial messages. Adding this to avoid exploding LLM costs while we track down
# the cause.
_SLACK_GREETINGS_TO_IGNORE = {
    "Welcome back!",
    "It's going to be a great day.",
    "Salutations!",
    "Greetings!",
    "Feeling great!",
    "Hi there",
    ":wave:",
}

# This is always (currently) the user id of Slack's official slackbot
_OFFICIAL_SLACKBOT_USER_ID = "USLACKBOT"


class SlackbotHandler:
    def __init__(self) -> None:
        logger.info("Initializing SlackbotHandler")
        self.tenant_ids: Set[str | None] = set()
        # The keys for these dictionaries are tuples of (tenant_id, slack_bot_id)
        self.socket_clients: Dict[tuple[str | None, int], TenantSocketModeClient] = {}
        self.slack_bot_tokens: Dict[tuple[str | None, int], SlackBotTokens] = {}

        # Store Redis lock objects here so we can release them properly
        self.redis_locks: Dict[str | None, Lock] = {}

        self.running = True
        self.pod_id = self.get_pod_id()
        self._shutdown_event = Event()
        logger.info(f"Pod ID: {self.pod_id}")

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        logger.info("Signal handlers registered")

        # Start the Prometheus metrics server
        logger.info("Starting Prometheus metrics server")
        start_http_server(8000)
        logger.info("Prometheus metrics server started")

        # Start background threads
        logger.info("Starting background threads")
        self.acquire_thread = threading.Thread(
            target=self.acquire_tenants_loop, daemon=True
        )
        self.heartbeat_thread = threading.Thread(
            target=self.heartbeat_loop, daemon=True
        )

        self.acquire_thread.start()
        self.heartbeat_thread.start()
        logger.info("Background threads started")

    def get_pod_id(self) -> str:
        pod_id = os.environ.get("HOSTNAME", "unknown_pod")
        logger.info(f"Retrieved pod ID: {pod_id}")
        return pod_id

    def acquire_tenants_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                self.acquire_tenants()

                # After we finish acquiring and managing Slack bots,
                # set the gauge to the number of active tenants (those with Slack bots).
                active_tenants_gauge.labels(namespace=POD_NAMESPACE, pod=POD_NAME).set(
                    len(self.tenant_ids)
                )
                logger.debug(
                    f"Current active tenants with Slack bots: {len(self.tenant_ids)}"
                )
            except Exception as e:
                logger.exception(f"Error in Slack acquisition: {e}")
            self._shutdown_event.wait(timeout=TENANT_ACQUISITION_INTERVAL)

    def heartbeat_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                self.send_heartbeats()
                logger.debug(
                    f"Sent heartbeats for {len(self.tenant_ids)} active tenants"
                )
            except Exception as e:
                logger.exception(f"Error in heartbeat loop: {e}")
            self._shutdown_event.wait(timeout=TENANT_HEARTBEAT_INTERVAL)

    def _manage_clients_per_tenant(
        self, db_session: Session, tenant_id: str | None, bot: SlackBot
    ) -> None:
        """
        - If the tokens are missing or empty, close the socket client and remove them.
        - If the tokens have changed, close the existing socket client and reconnect.
        - If the tokens are new, warm up the model and start a new socket client.
        """
        slack_bot_tokens = SlackBotTokens(
            bot_token=bot.bot_token,
            app_token=bot.app_token,
        )
        tenant_bot_pair = (tenant_id, bot.id)

        # If the tokens are missing or empty, close the socket client and remove them.
        if not slack_bot_tokens:
            logger.debug(
                f"No Slack bot tokens found for tenant={tenant_id}, bot {bot.id}"
            )
            if tenant_bot_pair in self.socket_clients:
                asyncio.run(self.socket_clients[tenant_bot_pair].close())
                del self.socket_clients[tenant_bot_pair]
                del self.slack_bot_tokens[tenant_bot_pair]
            return

        tokens_exist = tenant_bot_pair in self.slack_bot_tokens
        tokens_changed = (
            tokens_exist and slack_bot_tokens != self.slack_bot_tokens[tenant_bot_pair]
        )
        if not tokens_exist or tokens_changed:
            if tokens_exist:
                logger.info(
                    f"Slack Bot tokens changed for tenant={tenant_id}, bot {bot.id}; reconnecting"
                )
            else:
                # Warm up the model if needed
                search_settings = get_current_search_settings(db_session)
                embedding_model = EmbeddingModel.from_db_model(
                    search_settings=search_settings,
                    server_host=MODEL_SERVER_HOST,
                    server_port=MODEL_SERVER_PORT,
                )
                warm_up_bi_encoder(embedding_model=embedding_model)

            self.slack_bot_tokens[tenant_bot_pair] = slack_bot_tokens

            # Close any existing connection first
            if tenant_bot_pair in self.socket_clients:
                asyncio.run(self.socket_clients[tenant_bot_pair].close())

            self.start_socket_client(bot.id, tenant_id, slack_bot_tokens)

    def acquire_tenants(self) -> None:
        """
        - Attempt to acquire a Redis lock for each tenant.
        - If acquired, check if that tenant actually has Slack bots.
        - If yes, store them in self.tenant_ids and manage the socket connections.
        - If a tenant in self.tenant_ids no longer has Slack bots, remove it (and release the lock in this scope).
        """
        all_tenants = get_all_tenant_ids()

        token: Token[str]

        # 1) Try to acquire locks for new tenants
        for tenant_id in all_tenants:
            if (
                DISALLOWED_SLACK_BOT_TENANT_LIST is not None
                and tenant_id in DISALLOWED_SLACK_BOT_TENANT_LIST
            ):
                logger.debug(f"Tenant {tenant_id} is disallowed; skipping.")
                continue

            # Already acquired in a previous loop iteration?
            if tenant_id in self.tenant_ids:
                continue

            # Respect max tenant limit per pod
            if len(self.tenant_ids) >= MAX_TENANTS_PER_POD:
                logger.info(
                    f"Max tenants per pod reached ({MAX_TENANTS_PER_POD}); not acquiring more."
                )
                break

            redis_client = get_redis_client(tenant_id=tenant_id)
            # Acquire a Redis lock (non-blocking)
            rlock = redis_client.lock(
                OnyxRedisLocks.SLACK_BOT_LOCK, timeout=TENANT_LOCK_EXPIRATION
            )
            lock_acquired = rlock.acquire(blocking=False)

            if not lock_acquired and not DEV_MODE:
                logger.debug(
                    f"Another pod holds the lock for tenant {tenant_id}, skipping."
                )
                continue

            if lock_acquired:
                logger.debug(f"Acquired lock for tenant {tenant_id}.")
                self.redis_locks[tenant_id] = rlock
            else:
                # DEV_MODE will skip the lock acquisition guard
                logger.debug(
                    f"Running in DEV_MODE. Not enforcing lock for {tenant_id}."
                )

            # Now check if this tenant actually has Slack bots
            token = CURRENT_TENANT_ID_CONTEXTVAR.set(
                tenant_id or POSTGRES_DEFAULT_SCHEMA
            )
            try:
                with get_session_with_tenant(tenant_id) as db_session:
                    bots: list[SlackBot] = []
                    try:
                        bots = list(fetch_slack_bots(db_session=db_session))
                    except KvKeyNotFoundError:
                        # No Slackbot tokens, pass
                        pass
                    except Exception as e:
                        logger.exception(
                            f"Error fetching Slack bots for tenant {tenant_id}: {e}"
                        )

                    if bots:
                        # Mark as active tenant
                        self.tenant_ids.add(tenant_id)
                        for bot in bots:
                            self._manage_clients_per_tenant(
                                db_session=db_session,
                                tenant_id=tenant_id,
                                bot=bot,
                            )
                    else:
                        # If no Slack bots, release lock immediately (unless in DEV_MODE)
                        if lock_acquired and not DEV_MODE:
                            rlock.release()
                            del self.redis_locks[tenant_id]
                        logger.debug(
                            f"No Slack bots for tenant {tenant_id}; lock released (if held)."
                        )
            finally:
                CURRENT_TENANT_ID_CONTEXTVAR.reset(token)

        # 2) Make sure tenants we're handling still have Slack bots
        for tenant_id in list(self.tenant_ids):
            token = CURRENT_TENANT_ID_CONTEXTVAR.set(
                tenant_id or POSTGRES_DEFAULT_SCHEMA
            )
            redis_client = get_redis_client(tenant_id=tenant_id)

            try:
                with get_session_with_tenant(tenant_id) as db_session:
                    # Attempt to fetch Slack bots
                    try:
                        bots = list(fetch_slack_bots(db_session=db_session))
                    except KvKeyNotFoundError:
                        # No Slackbot tokens, pass (and remove below)
                        bots = []
                    except Exception as e:
                        logger.exception(f"Error handling tenant {tenant_id}: {e}")
                        bots = []

                    if not bots:
                        logger.info(
                            f"Tenant {tenant_id} no longer has Slack bots. Removing."
                        )
                        self._remove_tenant(tenant_id)

                        # NOTE: We release the lock here (in the same scope it was acquired)
                        if tenant_id in self.redis_locks and not DEV_MODE:
                            try:
                                self.redis_locks[tenant_id].release()
                                del self.redis_locks[tenant_id]
                                logger.info(f"Released lock for tenant {tenant_id}")
                            except Exception as e:
                                logger.error(
                                    f"Error releasing lock for tenant {tenant_id}: {e}"
                                )
                    else:
                        # Manage or reconnect Slack bot sockets
                        for bot in bots:
                            self._manage_clients_per_tenant(
                                db_session=db_session,
                                tenant_id=tenant_id,
                                bot=bot,
                            )
            finally:
                CURRENT_TENANT_ID_CONTEXTVAR.reset(token)

    def _remove_tenant(self, tenant_id: str | None) -> None:
        """
        Helper to remove a tenant from `self.tenant_ids` and close any socket clients.
        (Lock release now happens in `acquire_tenants()`, not here.)
        """
        # Close all socket clients for this tenant
        for (t_id, slack_bot_id), client in list(self.socket_clients.items()):
            if t_id == tenant_id:
                asyncio.run(client.close())
                del self.socket_clients[(t_id, slack_bot_id)]
                del self.slack_bot_tokens[(t_id, slack_bot_id)]
                logger.info(
                    f"Stopped SocketModeClient for tenant: {t_id}, app: {slack_bot_id}"
                )

        # Remove from active set
        if tenant_id in self.tenant_ids:
            self.tenant_ids.remove(tenant_id)

    def send_heartbeats(self) -> None:
        current_time = int(time.time())
        logger.debug(f"Sending heartbeats for {len(self.tenant_ids)} active tenants")
        for tenant_id in self.tenant_ids:
            redis_client = get_redis_client(tenant_id=tenant_id)
            heartbeat_key = f"{OnyxRedisLocks.SLACK_BOT_HEARTBEAT_PREFIX}:{self.pod_id}"
            redis_client.set(
                heartbeat_key, current_time, ex=TENANT_HEARTBEAT_EXPIRATION
            )

    def start_socket_client(
        self, slack_bot_id: int, tenant_id: str | None, slack_bot_tokens: SlackBotTokens
    ) -> None:
        socket_client: TenantSocketModeClient = _get_socket_client(
            slack_bot_tokens, tenant_id, slack_bot_id
        )

        try:
            bot_info = socket_client.web_client.auth_test()
            if bot_info["ok"]:
                bot_user_id = bot_info["user_id"]
                user_info = socket_client.web_client.users_info(user=bot_user_id)
                if user_info["ok"]:
                    bot_name = (
                        user_info["user"]["real_name"] or user_info["user"]["name"]
                    )
                    logger.info(
                        f"Started socket client for Slackbot with name '{bot_name}' (tenant: {tenant_id}, app: {slack_bot_id})"
                    )
        except Exception as e:
            logger.warning(
                f"Could not fetch bot name: {e} for tenant: {tenant_id}, app: {slack_bot_id}"
            )

        # Append the event handler
        process_slack_event = create_process_slack_event()
        socket_client.socket_mode_request_listeners.append(process_slack_event)  # type: ignore

        # Establish a WebSocket connection to the Socket Mode servers
        logger.info(
            f"Connecting socket client for tenant: {tenant_id}, app: {slack_bot_id}"
        )
        socket_client.connect()
        self.socket_clients[tenant_id, slack_bot_id] = socket_client
        # Ensure tenant is tracked as active
        self.tenant_ids.add(tenant_id)
        logger.info(
            f"Started SocketModeClient for tenant: {tenant_id}, app: {slack_bot_id}"
        )

    def stop_socket_clients(self) -> None:
        logger.info(f"Stopping {len(self.socket_clients)} socket clients")
        for (tenant_id, slack_bot_id), client in list(self.socket_clients.items()):
            asyncio.run(client.close())
            logger.info(
                f"Stopped SocketModeClient for tenant: {tenant_id}, app: {slack_bot_id}"
            )

    def shutdown(self, signum: int | None, frame: FrameType | None) -> None:
        if not self.running:
            return

        logger.info("Shutting down gracefully")
        self.running = False
        self._shutdown_event.set()

        # Stop all socket clients
        logger.info(f"Stopping {len(self.socket_clients)} socket clients")
        self.stop_socket_clients()

        # Release locks for all tenants we currently hold
        logger.info(f"Releasing locks for {len(self.tenant_ids)} tenants")
        for tenant_id in list(self.tenant_ids):
            if tenant_id in self.redis_locks:
                try:
                    self.redis_locks[tenant_id].release()
                    logger.info(f"Released lock for tenant {tenant_id}")
                except Exception as e:
                    logger.error(f"Error releasing lock for tenant {tenant_id}: {e}")
                finally:
                    del self.redis_locks[tenant_id]

        # Wait for background threads to finish (with a timeout)
        logger.info("Waiting for background threads to finish...")
        self.acquire_thread.join(timeout=5)
        self.heartbeat_thread.join(timeout=5)

        logger.info("Shutdown complete")
        sys.exit(0)


def prefilter_requests(req: SocketModeRequest, client: TenantSocketModeClient) -> bool:
    """True to keep going, False to ignore this Slack request"""
    if req.type == "events_api":
        # Verify channel is valid
        event = cast(dict[str, Any], req.payload.get("event", {}))
        msg = cast(str | None, event.get("text"))
        channel = cast(str | None, event.get("channel"))
        channel_specific_logger = setup_logger(extra={SLACK_CHANNEL_ID: channel})

        # This should never happen, but we can't continue without a channel since
        # we can't send a response without it
        if not channel:
            channel_specific_logger.warning("Found message without channel - skipping")
            return False

        if not msg:
            channel_specific_logger.warning(
                "Cannot respond to empty message - skipping"
            )
            return False

        if (
            req.payload.setdefault("event", {}).get("user", "")
            == _OFFICIAL_SLACKBOT_USER_ID
        ):
            channel_specific_logger.info(
                "Ignoring messages from Slack's official Slackbot"
            )
            return False

        if (
            msg in _SLACK_GREETINGS_TO_IGNORE
            or remove_onyx_bot_tag(msg, client=client.web_client)
            in _SLACK_GREETINGS_TO_IGNORE
        ):
            channel_specific_logger.error(
                f"Ignoring weird Slack greeting message: '{msg}'"
            )
            channel_specific_logger.error(
                f"Weird Slack greeting message payload: '{req.payload}'"
            )
            return False

        # Ensure that the message is a new message of expected type
        event_type = event.get("type")
        if event_type not in ["app_mention", "message"]:
            channel_specific_logger.info(
                f"Ignoring non-message event of type '{event_type}' for channel '{channel}'"
            )
            return False

        bot_tag_id = get_onyx_bot_slack_bot_id(client.web_client)
        if event_type == "message":
            is_dm = event.get("channel_type") == "im"
            is_tagged = bot_tag_id and bot_tag_id in msg
            is_onyx_bot_msg = bot_tag_id and bot_tag_id in event.get("user", "")

            # OnyxBot should never respond to itself
            if is_onyx_bot_msg:
                logger.info("Ignoring message from OnyxBot")
                return False

            # DMs with the bot don't pick up the @OnyxBot so we have to keep the
            # caught events_api
            if is_tagged and not is_dm:
                # Let the tag flow handle this case, don't reply twice
                return False

        # Check if this is a bot message (either via bot_profile or bot_message subtype)
        is_bot_message = bool(
            event.get("bot_profile") or event.get("subtype") == "bot_message"
        )
        if is_bot_message:
            channel_name, _ = get_channel_name_from_id(
                client=client.web_client, channel_id=channel
            )
            with get_session_with_tenant(client.tenant_id) as db_session:
                slack_channel_config = get_slack_channel_config_for_bot_and_channel(
                    db_session=db_session,
                    slack_bot_id=client.slack_bot_id,
                    channel_name=channel_name,
                )

            # If OnyxBot is not specifically tagged and the channel is not set to respond to bots, ignore the message
            if (not bot_tag_id or bot_tag_id not in msg) and (
                not slack_channel_config
                or not slack_channel_config.channel_config.get("respond_to_bots")
            ):
                channel_specific_logger.info(
                    "Ignoring message from bot since respond_to_bots is disabled"
                )
                return False

        # Ignore things like channel_join, channel_leave, etc.
        # NOTE: "file_share" is just a message with a file attachment, so we
        # should not ignore it
        message_subtype = event.get("subtype")
        if message_subtype not in [None, "file_share", "bot_message"]:
            channel_specific_logger.info(
                f"Ignoring message with subtype '{message_subtype}' since it is a special message type"
            )
            return False

        message_ts = event.get("ts")
        thread_ts = event.get("thread_ts")
        # Pick the root of the thread (if a thread exists)
        # Can respond in thread if it's an "im" directly to Onyx or @OnyxBot is tagged
        if (
            thread_ts
            and message_ts != thread_ts
            and event_type != "app_mention"
            and event.get("channel_type") != "im"
        ):
            channel_specific_logger.debug(
                "Skipping message since it is not the root of a thread"
            )
            return False

        msg = cast(str, event.get("text", ""))
        if not msg:
            channel_specific_logger.error("Unable to process empty message")
            return False

    if req.type == "slash_commands":
        # Verify that there's an associated channel
        channel = req.payload.get("channel_id")
        channel_specific_logger = setup_logger(extra={SLACK_CHANNEL_ID: channel})

        if not channel:
            channel_specific_logger.error(
                "Received OnyxBot command without channel - skipping"
            )
            return False

        sender = req.payload.get("user_id")
        if not sender:
            channel_specific_logger.error(
                "Cannot respond to OnyxBot command without sender to respond to."
            )
            return False

    if not check_message_limit():
        return False

    logger.debug(f"Handling Slack request with Payload: '{req.payload}'")
    return True


def process_feedback(req: SocketModeRequest, client: TenantSocketModeClient) -> None:
    if actions := req.payload.get("actions"):
        action = cast(dict[str, Any], actions[0])
        feedback_type = cast(str, action.get("action_id"))
        feedback_msg_reminder = cast(str, action.get("value"))
        feedback_id = cast(str, action.get("block_id"))
        channel_id = cast(str, req.payload["container"]["channel_id"])
        thread_ts = cast(str, req.payload["container"]["thread_ts"])
    else:
        logger.error("Unable to process feedback. Action not found")
        return

    user_id = cast(str, req.payload["user"]["id"])

    handle_slack_feedback(
        feedback_id=feedback_id,
        feedback_type=feedback_type,
        feedback_msg_reminder=feedback_msg_reminder,
        client=client.web_client,
        user_id_to_post_confirmation=user_id,
        channel_id_to_post_confirmation=channel_id,
        thread_ts_to_post_confirmation=thread_ts,
        tenant_id=client.tenant_id,
    )

    query_event_id, _, _ = decompose_action_id(feedback_id)
    logger.info(f"Successfully handled QA feedback for event: {query_event_id}")


def build_request_details(
    req: SocketModeRequest, client: TenantSocketModeClient
) -> SlackMessageInfo:
    if req.type == "events_api":
        event = cast(dict[str, Any], req.payload["event"])
        msg = cast(str, event["text"])
        channel = cast(str, event["channel"])
        tagged = event.get("type") == "app_mention"
        message_ts = event.get("ts")
        thread_ts = event.get("thread_ts")
        sender_id = event.get("user") or None
        expert_info = expert_info_from_slack_id(
            sender_id, client.web_client, user_cache={}
        )
        email = expert_info.email if expert_info else None

        msg = remove_onyx_bot_tag(msg, client=client.web_client)

        if DANSWER_BOT_REPHRASE_MESSAGE:
            logger.info(f"Rephrasing Slack message. Original message: {msg}")
            try:
                msg = rephrase_slack_message(msg)
                logger.info(f"Rephrased message: {msg}")
            except Exception as e:
                logger.error(f"Error while trying to rephrase the Slack message: {e}")
        else:
            logger.info(f"Received Slack message: {msg}")

        if tagged:
            logger.debug("User tagged OnyxBot")

        if thread_ts != message_ts and thread_ts is not None:
            thread_messages = read_slack_thread(
                channel=channel, thread=thread_ts, client=client.web_client
            )
        else:
            sender_display_name = None
            if expert_info:
                sender_display_name = expert_info.display_name
                if sender_display_name is None:
                    sender_display_name = (
                        f"{expert_info.first_name} {expert_info.last_name}"
                        if expert_info.last_name
                        else expert_info.first_name
                    )
                if sender_display_name is None:
                    sender_display_name = expert_info.email
            thread_messages = [
                ThreadMessage(
                    message=msg, sender=sender_display_name, role=MessageType.USER
                )
            ]

        return SlackMessageInfo(
            thread_messages=thread_messages,
            channel_to_respond=channel,
            msg_to_respond=cast(str, message_ts or thread_ts),
            thread_to_respond=cast(str, thread_ts or message_ts),
            sender_id=sender_id,
            email=email,
            bypass_filters=tagged,
            is_bot_msg=False,
            is_bot_dm=event.get("channel_type") == "im",
        )

    elif req.type == "slash_commands":
        channel = req.payload["channel_id"]
        msg = req.payload["text"]
        sender = req.payload["user_id"]
        expert_info = expert_info_from_slack_id(
            sender, client.web_client, user_cache={}
        )
        email = expert_info.email if expert_info else None

        single_msg = ThreadMessage(message=msg, sender=None, role=MessageType.USER)

        return SlackMessageInfo(
            thread_messages=[single_msg],
            channel_to_respond=channel,
            msg_to_respond=None,
            thread_to_respond=None,
            sender_id=sender,
            email=email,
            bypass_filters=True,
            is_bot_msg=True,
            is_bot_dm=False,
        )

    raise RuntimeError("Programming fault, this should never happen.")


def apologize_for_fail(
    details: SlackMessageInfo,
    client: TenantSocketModeClient,
) -> None:
    respond_in_thread(
        client=client.web_client,
        channel=details.channel_to_respond,
        thread_ts=details.msg_to_respond,
        text="Sorry, we weren't able to find anything relevant :cold_sweat:",
    )


def process_message(
    req: SocketModeRequest,
    client: TenantSocketModeClient,
    respond_every_channel: bool = DANSWER_BOT_RESPOND_EVERY_CHANNEL,
    notify_no_answer: bool = NOTIFY_SLACKBOT_NO_ANSWER,
) -> None:
    logger.debug(
        f"Received Slack request of type: '{req.type}' for tenant, {client.tenant_id}"
    )

    # Throw out requests that can't or shouldn't be handled
    if not prefilter_requests(req, client):
        return

    details = build_request_details(req, client)
    channel = details.channel_to_respond
    channel_name, is_dm = get_channel_name_from_id(
        client=client.web_client, channel_id=channel
    )

    token: Token[str] | None = None
    # Set the current tenant ID at the beginning for all DB calls within this thread
    if client.tenant_id:
        logger.info(f"Setting tenant ID to {client.tenant_id}")
        token = CURRENT_TENANT_ID_CONTEXTVAR.set(client.tenant_id)
    try:
        with get_session_with_tenant(client.tenant_id) as db_session:
            slack_channel_config = get_slack_channel_config_for_bot_and_channel(
                db_session=db_session,
                slack_bot_id=client.slack_bot_id,
                channel_name=channel_name,
            )

            follow_up = bool(
                slack_channel_config.channel_config
                and slack_channel_config.channel_config.get("follow_up_tags")
                is not None
            )
            feedback_reminder_id = schedule_feedback_reminder(
                details=details, client=client.web_client, include_followup=follow_up
            )

            failed = handle_message(
                message_info=details,
                slack_channel_config=slack_channel_config,
                client=client.web_client,
                feedback_reminder_id=feedback_reminder_id,
                tenant_id=client.tenant_id,
            )

            if failed:
                if feedback_reminder_id:
                    remove_scheduled_feedback_reminder(
                        client=client.web_client,
                        channel=details.sender_id,
                        msg_id=feedback_reminder_id,
                    )
                # Skipping answering due to pre-filtering is not considered a failure
                if notify_no_answer:
                    apologize_for_fail(details, client)
    finally:
        if token:
            CURRENT_TENANT_ID_CONTEXTVAR.reset(token)


def acknowledge_message(req: SocketModeRequest, client: TenantSocketModeClient) -> None:
    response = SocketModeResponse(envelope_id=req.envelope_id)
    client.send_socket_mode_response(response)


def action_routing(req: SocketModeRequest, client: TenantSocketModeClient) -> None:
    if actions := req.payload.get("actions"):
        action = cast(dict[str, Any], actions[0])

        if action["action_id"] in [DISLIKE_BLOCK_ACTION_ID, LIKE_BLOCK_ACTION_ID]:
            # AI Answer feedback
            return process_feedback(req, client)
        elif action["action_id"] == FEEDBACK_DOC_BUTTON_BLOCK_ACTION_ID:
            # Activation of the "source feedback" button
            return handle_doc_feedback_button(req, client)
        elif action["action_id"] == FOLLOWUP_BUTTON_ACTION_ID:
            return handle_followup_button(req, client)
        elif action["action_id"] == IMMEDIATE_RESOLVED_BUTTON_ACTION_ID:
            return handle_followup_resolved_button(req, client, immediate=True)
        elif action["action_id"] == FOLLOWUP_BUTTON_RESOLVED_ACTION_ID:
            return handle_followup_resolved_button(req, client, immediate=False)
        elif action["action_id"] == GENERATE_ANSWER_BUTTON_ACTION_ID:
            return handle_generate_answer_button(req, client)


def view_routing(req: SocketModeRequest, client: TenantSocketModeClient) -> None:
    if view := req.payload.get("view"):
        if view["callback_id"] == VIEW_DOC_FEEDBACK_ID:
            return process_feedback(req, client)


def create_process_slack_event() -> (
    Callable[[TenantSocketModeClient, SocketModeRequest], None]
):
    def process_slack_event(
        client: TenantSocketModeClient, req: SocketModeRequest
    ) -> None:
        # Always respond right away, if Slack doesn't receive these frequently enough
        # it will assume the Bot is DEAD!!! :(
        acknowledge_message(req, client)

        try:
            if req.type == "interactive":
                if req.payload.get("type") == "block_actions":
                    return action_routing(req, client)
                elif req.payload.get("type") == "view_submission":
                    return view_routing(req, client)
            elif req.type == "events_api" or req.type == "slash_commands":
                return process_message(req, client)
        except Exception:
            logger.exception("Failed to process slack event")

    return process_slack_event


def _get_socket_client(
    slack_bot_tokens: SlackBotTokens, tenant_id: str | None, slack_bot_id: int
) -> TenantSocketModeClient:
    # For more info on how to set this up, checkout the docs:
    # https://docs.onyx.app/slack_bot_setup
    return TenantSocketModeClient(
        # This app-level token will be used only for establishing a connection
        app_token=slack_bot_tokens.app_token,
        web_client=WebClient(token=slack_bot_tokens.bot_token),
        tenant_id=tenant_id,
        slack_bot_id=slack_bot_id,
    )


if __name__ == "__main__":
    # Initialize the tenant handler which will manage tenant connections
    logger.info("Starting SlackbotHandler")
    tenant_handler = SlackbotHandler()

    set_is_ee_based_on_env_variable()

    logger.info("Verifying query preprocessing (NLTK) data is downloaded")
    download_nltk_data()

    try:
        # Keep the main thread alive
        while tenant_handler.running:
            time.sleep(1)

    except Exception:
        logger.exception("Fatal error in main thread")
        tenant_handler.shutdown(None, None)
