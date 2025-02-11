from collections.abc import Callable
from typing import cast

from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from pydantic import BaseModel
from pydantic.v1 import BaseModel as BaseModel__v1

from onyx.chat.models import PromptConfig
from onyx.chat.prompt_builder.citations_prompt import compute_max_llm_input_tokens
from onyx.chat.prompt_builder.utils import translate_history_to_basemessages
from onyx.file_store.models import InMemoryChatFile
from onyx.llm.interfaces import LLMConfig
from onyx.llm.models import PreviousMessage
from onyx.llm.utils import build_content_with_imgs
from onyx.llm.utils import check_message_tokens
from onyx.llm.utils import message_to_prompt_and_imgs
from onyx.llm.utils import model_supports_image_input
from onyx.natural_language_processing.utils import get_tokenizer
from onyx.prompts.chat_prompts import CHAT_USER_CONTEXT_FREE_PROMPT
from onyx.prompts.direct_qa_prompts import HISTORY_BLOCK
from onyx.prompts.prompt_utils import drop_messages_history_overflow
from onyx.prompts.prompt_utils import handle_onyx_date_awareness
from onyx.tools.force import ForceUseTool
from onyx.tools.models import ToolCallFinalResult
from onyx.tools.models import ToolCallKickoff
from onyx.tools.models import ToolResponse
from onyx.tools.tool import Tool


def default_build_system_message(
    prompt_config: PromptConfig,
) -> SystemMessage | None:
    system_prompt = prompt_config.system_prompt.strip()
    tag_handled_prompt = handle_onyx_date_awareness(
        system_prompt,
        prompt_config,
        add_additional_info_if_no_tag=prompt_config.datetime_aware,
    )

    if not tag_handled_prompt:
        return None

    return SystemMessage(content=tag_handled_prompt)


def default_build_user_message(
    user_query: str,
    prompt_config: PromptConfig,
    files: list[InMemoryChatFile] = [],
    single_message_history: str | None = None,
) -> HumanMessage:
    history_block = (
        HISTORY_BLOCK.format(history_str=single_message_history)
        if single_message_history
        else ""
    )

    user_prompt = (
        CHAT_USER_CONTEXT_FREE_PROMPT.format(
            history_block=history_block,
            task_prompt=prompt_config.task_prompt,
            user_query=user_query,
        )
        if prompt_config.task_prompt
        else user_query
    )
    user_prompt = user_prompt.strip()
    tag_handled_prompt = handle_onyx_date_awareness(user_prompt, prompt_config)
    user_msg = HumanMessage(
        content=build_content_with_imgs(tag_handled_prompt, files)
        if files
        else tag_handled_prompt
    )
    return user_msg


class AnswerPromptBuilder:
    def __init__(
        self,
        user_message: HumanMessage,
        message_history: list[PreviousMessage],
        llm_config: LLMConfig,
        raw_user_query: str,
        raw_user_uploaded_files: list[InMemoryChatFile],
        single_message_history: str | None = None,
        system_message: SystemMessage | None = None,
    ) -> None:
        self.max_tokens = compute_max_llm_input_tokens(llm_config)

        llm_tokenizer = get_tokenizer(
            provider_type=llm_config.model_provider,
            model_name=llm_config.model_name,
        )
        self.llm_config = llm_config
        self.llm_tokenizer_encode_func = cast(
            Callable[[str], list[int]], llm_tokenizer.encode
        )

        self.raw_message_history = message_history
        (
            self.message_history,
            self.history_token_cnts,
        ) = translate_history_to_basemessages(
            message_history,
            exclude_images=not model_supports_image_input(
                self.llm_config.model_name,
                self.llm_config.model_provider,
            ),
        )

        self.system_message_and_token_cnt: tuple[SystemMessage, int] | None = (
            (
                system_message,
                check_message_tokens(system_message, self.llm_tokenizer_encode_func),
            )
            if system_message
            else None
        )
        self.user_message_and_token_cnt = (
            user_message,
            check_message_tokens(
                user_message,
                self.llm_tokenizer_encode_func,
            ),
        )

        self.new_messages_and_token_cnts: list[tuple[BaseMessage, int]] = []

        # used for building a new prompt after a tool-call
        self.raw_user_query = raw_user_query
        self.raw_user_uploaded_files = raw_user_uploaded_files
        self.single_message_history = single_message_history

    def update_system_prompt(self, system_message: SystemMessage | None) -> None:
        if not system_message:
            self.system_message_and_token_cnt = None
            return

        self.system_message_and_token_cnt = (
            system_message,
            check_message_tokens(system_message, self.llm_tokenizer_encode_func),
        )

    def update_user_prompt(self, user_message: HumanMessage) -> None:
        self.user_message_and_token_cnt = (
            user_message,
            check_message_tokens(user_message, self.llm_tokenizer_encode_func),
        )

    def append_message(self, message: BaseMessage) -> None:
        """Append a new message to the message history."""
        token_count = check_message_tokens(message, self.llm_tokenizer_encode_func)
        self.new_messages_and_token_cnts.append((message, token_count))

    def get_user_message_content(self) -> str:
        query, _ = message_to_prompt_and_imgs(self.user_message_and_token_cnt[0])
        return query

    def build(self) -> list[BaseMessage]:
        if not self.user_message_and_token_cnt:
            raise ValueError("User message must be set before building prompt")

        final_messages_with_tokens: list[tuple[BaseMessage, int]] = []
        if self.system_message_and_token_cnt:
            final_messages_with_tokens.append(self.system_message_and_token_cnt)

        final_messages_with_tokens.extend(
            [
                (self.message_history[i], self.history_token_cnts[i])
                for i in range(len(self.message_history))
            ]
        )

        final_messages_with_tokens.append(self.user_message_and_token_cnt)

        if self.new_messages_and_token_cnts:
            final_messages_with_tokens.extend(self.new_messages_and_token_cnts)

        return drop_messages_history_overflow(
            final_messages_with_tokens, self.max_tokens
        )


# Stores some parts of a prompt builder as needed for tool calls
class PromptSnapshot(BaseModel):
    raw_message_history: list[PreviousMessage]
    raw_user_query: str
    built_prompt: list[BaseMessage]


# TODO: rename this? AnswerConfig maybe?
class LLMCall(BaseModel__v1):
    prompt_builder: AnswerPromptBuilder
    tools: list[Tool]
    force_use_tool: ForceUseTool
    files: list[InMemoryChatFile]
    tool_call_info: list[ToolCallKickoff | ToolResponse | ToolCallFinalResult]
    using_tool_calling_llm: bool

    class Config:
        arbitrary_types_allowed = True
