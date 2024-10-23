import time
from threading import Event
from typing import Any
from typing import cast

from slack_sdk import WebClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from danswer.configs.constants import MessageType
from danswer.configs.danswerbot_configs import DANSWER_BOT_REPHRASE_MESSAGE
from danswer.configs.danswerbot_configs import DANSWER_BOT_RESPOND_EVERY_CHANNEL
from danswer.configs.danswerbot_configs import NOTIFY_SLACKBOT_NO_ANSWER
from danswer.connectors.slack.utils import expert_info_from_slack_id
from danswer.danswerbot.slack.config import get_slack_bot_config_for_channel
from danswer.danswerbot.slack.constants import DISLIKE_BLOCK_ACTION_ID
from danswer.danswerbot.slack.constants import FEEDBACK_DOC_BUTTON_BLOCK_ACTION_ID
from danswer.danswerbot.slack.constants import FOLLOWUP_BUTTON_ACTION_ID
from danswer.danswerbot.slack.constants import FOLLOWUP_BUTTON_RESOLVED_ACTION_ID
from danswer.danswerbot.slack.constants import GENERATE_ANSWER_BUTTON_ACTION_ID
from danswer.danswerbot.slack.constants import IMMEDIATE_RESOLVED_BUTTON_ACTION_ID
from danswer.danswerbot.slack.constants import LIKE_BLOCK_ACTION_ID
from danswer.danswerbot.slack.constants import VIEW_DOC_FEEDBACK_ID
from danswer.danswerbot.slack.handlers.handle_buttons import handle_doc_feedback_button
from danswer.danswerbot.slack.handlers.handle_buttons import handle_followup_button
from danswer.danswerbot.slack.handlers.handle_buttons import (
    handle_followup_resolved_button,
)
from danswer.danswerbot.slack.handlers.handle_buttons import (
    handle_generate_answer_button,
)
from danswer.danswerbot.slack.handlers.handle_buttons import handle_slack_feedback
from danswer.danswerbot.slack.handlers.handle_message import handle_message
from danswer.danswerbot.slack.handlers.handle_message import (
    remove_scheduled_feedback_reminder,
)
from danswer.danswerbot.slack.handlers.handle_message import schedule_feedback_reminder
from danswer.danswerbot.slack.models import SlackMessageInfo
from danswer.danswerbot.slack.tokens import fetch_tokens
from danswer.danswerbot.slack.utils import check_message_limit
from danswer.danswerbot.slack.utils import decompose_action_id
from danswer.danswerbot.slack.utils import get_channel_name_from_id
from danswer.danswerbot.slack.utils import get_danswer_bot_app_id
from danswer.danswerbot.slack.utils import read_slack_thread
from danswer.danswerbot.slack.utils import remove_danswer_bot_tag
from danswer.danswerbot.slack.utils import rephrase_slack_message
from danswer.danswerbot.slack.utils import respond_in_thread
from danswer.danswerbot.slack.utils import TenantSocketModeClient
from danswer.db.engine import get_all_tenant_ids
from danswer.db.engine import get_session_with_tenant
from danswer.db.search_settings import get_current_search_settings
from danswer.key_value_store.interface import KvKeyNotFoundError
from danswer.natural_language_processing.search_nlp_models import EmbeddingModel
from danswer.natural_language_processing.search_nlp_models import warm_up_bi_encoder
from danswer.one_shot_answer.models import ThreadMessage
from danswer.search.retrieval.search_runner import download_nltk_data
from danswer.server.manage.models import SlackBotTokens
from danswer.utils.logger import setup_logger
from danswer.utils.variable_functionality import set_is_ee_based_on_env_variable
from shared_configs.configs import current_tenant_id
from shared_configs.configs import MODEL_SERVER_HOST
from shared_configs.configs import MODEL_SERVER_PORT
from shared_configs.configs import SLACK_CHANNEL_ID

logger = setup_logger()

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

# this is always (currently) the user id of Slack's official slackbot
_OFFICIAL_SLACKBOT_USER_ID = "USLACKBOT"


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
            or remove_danswer_bot_tag(msg, client=client.web_client)
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

        bot_tag_id = get_danswer_bot_app_id(client.web_client)
        if event_type == "message":
            is_dm = event.get("channel_type") == "im"
            is_tagged = bot_tag_id and bot_tag_id in msg
            is_danswer_bot_msg = bot_tag_id and bot_tag_id in event.get("user", "")

            # DanswerBot should never respond to itself
            if is_danswer_bot_msg:
                logger.info("Ignoring message from DanswerBot")
                return False

            # DMs with the bot don't pick up the @DanswerBot so we have to keep the
            # caught events_api
            if is_tagged and not is_dm:
                # Let the tag flow handle this case, don't reply twice
                return False

        if event.get("bot_profile"):
            channel_name, _ = get_channel_name_from_id(
                client=client.web_client, channel_id=channel
            )

            with get_session_with_tenant(client.tenant_id) as db_session:
                slack_bot_config = get_slack_bot_config_for_channel(
                    channel_name=channel_name, db_session=db_session
                )
            # If DanswerBot is not specifically tagged and the channel is not set to respond to bots, ignore the message
            if (not bot_tag_id or bot_tag_id not in msg) and (
                not slack_bot_config
                or not slack_bot_config.channel_config.get("respond_to_bots")
            ):
                channel_specific_logger.info("Ignoring message from bot")
                return False

        # Ignore things like channel_join, channel_leave, etc.
        # NOTE: "file_share" is just a message with a file attachment, so we
        # should not ignore it
        message_subtype = event.get("subtype")
        if message_subtype not in [None, "file_share"]:
            channel_specific_logger.info(
                f"Ignoring message with subtype '{message_subtype}' since is is a special message type"
            )
            return False

        message_ts = event.get("ts")
        thread_ts = event.get("thread_ts")
        # Pick the root of the thread (if a thread exists)
        # Can respond in thread if it's an "im" directly to Danswer or @DanswerBot is tagged
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
                "Received DanswerBot command without channel - skipping"
            )
            return False

        sender = req.payload.get("user_id")
        if not sender:
            channel_specific_logger.error(
                "Cannot respond to DanswerBot command without sender to respond to."
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
    logger.notice(f"Successfully handled QA feedback for event: {query_event_id}")


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
        sender = event.get("user") or None
        expert_info = expert_info_from_slack_id(
            sender, client.web_client, user_cache={}
        )
        email = expert_info.email if expert_info else None

        msg = remove_danswer_bot_tag(msg, client=client.web_client)

        if DANSWER_BOT_REPHRASE_MESSAGE:
            logger.notice(f"Rephrasing Slack message. Original message: {msg}")
            try:
                msg = rephrase_slack_message(msg)
                logger.notice(f"Rephrased message: {msg}")
            except Exception as e:
                logger.error(f"Error while trying to rephrase the Slack message: {e}")
        else:
            logger.notice(f"Received Slack message: {msg}")

        if tagged:
            logger.debug("User tagged DanswerBot")

        if thread_ts != message_ts and thread_ts is not None:
            thread_messages = read_slack_thread(
                channel=channel, thread=thread_ts, client=client.web_client
            )
        else:
            thread_messages = [
                ThreadMessage(message=msg, sender=None, role=MessageType.USER)
            ]

        return SlackMessageInfo(
            thread_messages=thread_messages,
            channel_to_respond=channel,
            msg_to_respond=cast(str, message_ts or thread_ts),
            thread_to_respond=cast(str, thread_ts or message_ts),
            sender=sender,
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
            sender=sender,
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

    # Set the current tenant ID at the beginning for all DB calls within this thread
    if client.tenant_id:
        logger.info(f"Setting tenant ID to {client.tenant_id}")
        token = current_tenant_id.set(client.tenant_id)
    try:
        with get_session_with_tenant(client.tenant_id) as db_session:
            slack_bot_config = get_slack_bot_config_for_channel(
                channel_name=channel_name, db_session=db_session
            )

            # Be careful about this default, don't want to accidentally spam every channel
            # Users should be able to DM slack bot in their private channels though
            if (
                slack_bot_config is None
                and not respond_every_channel
                # Can't have configs for DMs so don't toss them out
                and not is_dm
                # If /DanswerBot (is_bot_msg) or @DanswerBot (bypass_filters)
                # always respond with the default configs
                and not (details.is_bot_msg or details.bypass_filters)
            ):
                return

            follow_up = bool(
                slack_bot_config
                and slack_bot_config.channel_config
                and slack_bot_config.channel_config.get("follow_up_tags") is not None
            )
            feedback_reminder_id = schedule_feedback_reminder(
                details=details, client=client.web_client, include_followup=follow_up
            )

            failed = handle_message(
                message_info=details,
                slack_bot_config=slack_bot_config,
                client=client.web_client,
                feedback_reminder_id=feedback_reminder_id,
                tenant_id=client.tenant_id,
            )

            if failed:
                if feedback_reminder_id:
                    remove_scheduled_feedback_reminder(
                        client=client.web_client,
                        channel=details.sender,
                        msg_id=feedback_reminder_id,
                    )
                # Skipping answering due to pre-filtering is not considered a failure
                if notify_no_answer:
                    apologize_for_fail(details, client)
    finally:
        if client.tenant_id:
            current_tenant_id.reset(token)


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


def process_slack_event(client: TenantSocketModeClient, req: SocketModeRequest) -> None:
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
    except Exception as e:
        logger.exception(f"Failed to process slack event. Error: {e}")
        logger.error(f"Slack request payload: {req.payload}")


def _get_socket_client(
    slack_bot_tokens: SlackBotTokens, tenant_id: str | None
) -> TenantSocketModeClient:
    # For more info on how to set this up, checkout the docs:
    # https://docs.danswer.dev/slack_bot_setup
    return TenantSocketModeClient(
        # This app-level token will be used only for establishing a connection
        app_token=slack_bot_tokens.app_token,
        web_client=WebClient(token=slack_bot_tokens.bot_token),
        tenant_id=tenant_id,
    )


def _initialize_socket_client(socket_client: TenantSocketModeClient) -> None:
    socket_client.socket_mode_request_listeners.append(process_slack_event)  # type: ignore

    # Establish a WebSocket connection to the Socket Mode servers
    logger.notice(f"Listening for messages from Slack {socket_client.tenant_id }...")
    socket_client.connect()


# Follow the guide (https://docs.danswer.dev/slack_bot_setup) to set up
# the slack bot in your workspace, and then add the bot to any channels you want to
# try and answer questions for. Running this file will setup Danswer to listen to all
# messages in those channels and attempt to answer them. As of now, it will only respond
# to messages sent directly in the channel - it will not respond to messages sent within a
# thread.
#
# NOTE: we are using Web Sockets so that you can run this from within a firewalled VPC
# without issue.
if __name__ == "__main__":
    slack_bot_tokens: dict[str | None, SlackBotTokens] = {}
    socket_clients: dict[str | None, TenantSocketModeClient] = {}

    set_is_ee_based_on_env_variable()

    logger.notice("Verifying query preprocessing (NLTK) data is downloaded")
    download_nltk_data()

    while True:
        try:
            tenant_ids = get_all_tenant_ids()  # Function to retrieve all tenant IDs

            for tenant_id in tenant_ids:
                with get_session_with_tenant(tenant_id) as db_session:
                    try:
                        token = current_tenant_id.set(tenant_id or "public")
                        latest_slack_bot_tokens = fetch_tokens()
                        current_tenant_id.reset(token)

                        if (
                            tenant_id not in slack_bot_tokens
                            or latest_slack_bot_tokens != slack_bot_tokens[tenant_id]
                        ):
                            if tenant_id in slack_bot_tokens:
                                logger.notice(
                                    f"Slack Bot tokens have changed for tenant {tenant_id} - reconnecting"
                                )
                            else:
                                # Initial setup for this tenant
                                search_settings = get_current_search_settings(
                                    db_session
                                )
                                embedding_model = EmbeddingModel.from_db_model(
                                    search_settings=search_settings,
                                    server_host=MODEL_SERVER_HOST,
                                    server_port=MODEL_SERVER_PORT,
                                )
                                warm_up_bi_encoder(embedding_model=embedding_model)

                            slack_bot_tokens[tenant_id] = latest_slack_bot_tokens

                            # potentially may cause a message to be dropped, but it is complicated
                            # to avoid + (1) if the user is changing tokens, they are likely okay with some
                            # "migration downtime" and (2) if a single message is lost it is okay
                            # as this should be a very rare occurrence
                            if tenant_id in socket_clients:
                                socket_clients[tenant_id].close()

                            socket_client = _get_socket_client(
                                latest_slack_bot_tokens, tenant_id
                            )

                            # Initialize socket client for this tenant. Each tenant has its own
                            # socket client, allowing for multiple concurrent connections (one
                            # per tenant) with the tenant ID wrapped in the socket model client.
                            # Each `connect` stores websocket connection in a separate thread.
                            _initialize_socket_client(socket_client)

                            socket_clients[tenant_id] = socket_client

                    except KvKeyNotFoundError:
                        logger.debug(f"Missing Slack Bot tokens for tenant {tenant_id}")
                        if tenant_id in socket_clients:
                            socket_clients[tenant_id].disconnect()
                            del socket_clients[tenant_id]
                            del slack_bot_tokens[tenant_id]

            # Wait before checking for updates
            Event().wait(timeout=60)

        except Exception:
            logger.exception("An error occurred outside of main event loop")
            time.sleep(60)
