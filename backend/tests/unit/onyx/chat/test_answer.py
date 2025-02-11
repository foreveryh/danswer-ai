import json
from typing import cast
from unittest.mock import MagicMock
from unittest.mock import Mock
from uuid import UUID

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from langchain_core.messages import ToolCall
from langchain_core.messages import ToolCallChunk
from sqlalchemy.orm import Session

from onyx.chat.answer import Answer
from onyx.chat.models import AnswerStyleConfig
from onyx.chat.models import CitationInfo
from onyx.chat.models import LlmDoc
from onyx.chat.models import OnyxAnswerPiece
from onyx.chat.models import OnyxContexts
from onyx.chat.models import PromptConfig
from onyx.chat.models import StreamStopInfo
from onyx.chat.models import StreamStopReason
from onyx.chat.prompt_builder.answer_prompt_builder import AnswerPromptBuilder
from onyx.chat.prompt_builder.answer_prompt_builder import default_build_system_message
from onyx.chat.prompt_builder.answer_prompt_builder import default_build_user_message
from onyx.context.search.models import SearchRequest
from onyx.llm.interfaces import LLM
from onyx.tools.force import ForceUseTool
from onyx.tools.models import ToolCallFinalResult
from onyx.tools.models import ToolCallKickoff
from onyx.tools.models import ToolResponse
from onyx.tools.tool_implementations.search.search_tool import SEARCH_DOC_CONTENT_ID
from onyx.tools.tool_implementations.search_like_tool_utils import (
    FINAL_CONTEXT_DOCUMENTS_ID,
)
from tests.unit.onyx.chat.conftest import DEFAULT_SEARCH_ARGS
from tests.unit.onyx.chat.conftest import QUERY


@pytest.fixture
def answer_instance(
    mock_llm: LLM,
    answer_style_config: AnswerStyleConfig,
    prompt_config: PromptConfig,
) -> Answer:
    return Answer(
        prompt_builder=AnswerPromptBuilder(
            user_message=default_build_user_message(
                user_query=QUERY,
                prompt_config=prompt_config,
                files=[],
                single_message_history=None,
            ),
            system_message=default_build_system_message(prompt_config),
            message_history=[],
            llm_config=mock_llm.config,
            raw_user_query=QUERY,
            raw_user_uploaded_files=[],
        ),
        db_session=Mock(spec=Session),
        answer_style_config=answer_style_config,
        llm=mock_llm,
        fast_llm=mock_llm,
        force_use_tool=ForceUseTool(force_use=False, tool_name="", args=None),
        search_request=SearchRequest(query=QUERY),
        chat_session_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
        current_agent_message_id=0,
    )


def test_basic_answer(answer_instance: Answer) -> None:
    mock_llm = cast(Mock, answer_instance.graph_config.tooling.primary_llm)
    mock_llm.stream.return_value = [
        AIMessageChunk(content="This is a "),
        AIMessageChunk(content="mock answer."),
    ]
    answer_instance.graph_config.tooling.fast_llm = mock_llm
    answer_instance.graph_config.tooling.primary_llm = mock_llm

    output = list(answer_instance.processed_streamed_output)
    assert len(output) == 2
    assert isinstance(output[0], OnyxAnswerPiece)
    assert isinstance(output[1], OnyxAnswerPiece)

    full_answer = "".join(
        piece.answer_piece
        for piece in output
        if isinstance(piece, OnyxAnswerPiece) and piece.answer_piece is not None
    )
    assert full_answer == "This is a mock answer."

    assert answer_instance.llm_answer == "This is a mock answer."
    assert answer_instance.citations == []

    assert mock_llm.stream.call_count == 1
    mock_llm.stream.assert_called_once_with(
        prompt=[
            SystemMessage(content="System prompt"),
            HumanMessage(content="Task prompt\n\nQUERY:\nTest question"),
        ],
        tools=None,
        tool_choice=None,
        structured_response_format=None,
    )


@pytest.mark.parametrize(
    "force_use_tool, expected_tool_args",
    [
        (
            ForceUseTool(force_use=False, tool_name="", args=None),
            DEFAULT_SEARCH_ARGS,
        ),
        (
            ForceUseTool(
                force_use=True, tool_name="search", args={"query": "forced search"}
            ),
            {"query": "forced search"},
        ),
    ],
)
def test_answer_with_search_call(
    answer_instance: Answer,
    mock_search_results: list[LlmDoc],
    mock_contexts: OnyxContexts,
    mock_search_tool: MagicMock,
    force_use_tool: ForceUseTool,
    expected_tool_args: dict,
) -> None:
    answer_instance.graph_config.tooling.tools = [mock_search_tool]
    answer_instance.graph_config.tooling.force_use_tool = force_use_tool

    # Set up the LLM mock to return search results and then an answer
    mock_llm = cast(Mock, answer_instance.graph_config.tooling.primary_llm)

    stream_side_effect: list[list[BaseMessage]] = []

    if not force_use_tool.force_use:
        tool_call_chunk = AIMessageChunk(content="")
        tool_call_chunk.tool_calls = [
            ToolCall(
                id="search",
                name="search",
                args=expected_tool_args,
            )
        ]
        tool_call_chunk.tool_call_chunks = [
            ToolCallChunk(
                id="search",
                name="search",
                args=json.dumps(expected_tool_args),
                index=0,
            )
        ]
        stream_side_effect.append([tool_call_chunk])

    stream_side_effect.append(
        [
            AIMessageChunk(content="Based on the search results, "),
            AIMessageChunk(content="the answer is abc[1]. "),
            AIMessageChunk(content="This is some other stuff."),
        ],
    )
    mock_llm.stream.side_effect = stream_side_effect

    print("side effect")
    for v in stream_side_effect:
        print(v)
        print("-" * 300)
    print(len(stream_side_effect))
    print("-" * 300)
    # Process the output
    output = list(answer_instance.processed_streamed_output)

    # Updated assertions
    # assert len(output) == 7
    assert output[0] == ToolCallKickoff(
        tool_name="search", tool_args=expected_tool_args
    )
    assert output[1] == ToolResponse(
        id=SEARCH_DOC_CONTENT_ID,
        response=mock_contexts,
    )
    assert output[2] == ToolResponse(
        id="final_context_documents",
        response=mock_search_results,
    )
    assert output[3] == ToolCallFinalResult(
        tool_name="search",
        tool_args=expected_tool_args,
        tool_result=[json.loads(doc.model_dump_json()) for doc in mock_search_results],
    )
    assert output[4] == OnyxAnswerPiece(answer_piece="Based on the search results, ")
    expected_citation = CitationInfo(citation_num=1, document_id="doc1")
    assert output[5] == expected_citation
    assert output[6] == OnyxAnswerPiece(
        answer_piece="the answer is abc[[1]](https://example.com/doc1). "
    )
    assert output[7] == OnyxAnswerPiece(answer_piece="This is some other stuff.")

    expected_answer = (
        "Based on the search results, "
        "the answer is abc[[1]](https://example.com/doc1). "
        "This is some other stuff."
    )
    full_answer = "".join(
        piece.answer_piece
        for piece in output
        if isinstance(piece, OnyxAnswerPiece) and piece.answer_piece is not None
    )
    assert full_answer == expected_answer

    assert answer_instance.llm_answer == expected_answer
    assert len(answer_instance.citations) == 1
    assert answer_instance.citations[0] == expected_citation

    # Verify LLM calls
    if not force_use_tool.force_use:
        assert mock_llm.stream.call_count == 2
        first_call, second_call = mock_llm.stream.call_args_list

        # First call should include the search tool definition
        assert len(first_call.kwargs["tools"]) == 1
        assert (
            first_call.kwargs["tools"][0]
            == mock_search_tool.tool_definition.return_value
        )

        # Second call should not include tools (as we're just generating the final answer)
        assert "tools" not in second_call.kwargs or not second_call.kwargs["tools"]
        # Second call should use the returned prompt from build_next_prompt
        assert (
            second_call.kwargs["prompt"]
            == mock_search_tool.build_next_prompt.return_value.build.return_value
        )

        # Verify that tool_definition was called on the mock_search_tool
        mock_search_tool.tool_definition.assert_called_once()
    else:
        assert mock_llm.stream.call_count == 1

        call = mock_llm.stream.call_args_list[0]
        assert (
            call.kwargs["prompt"]
            == mock_search_tool.build_next_prompt.return_value.build.return_value
        )


def test_answer_with_search_no_tool_calling(
    answer_instance: Answer,
    mock_search_results: list[LlmDoc],
    mock_contexts: OnyxContexts,
    mock_search_tool: MagicMock,
) -> None:
    answer_instance.graph_config.tooling.tools = [mock_search_tool]

    # Set up the LLM mock to return an answer
    mock_llm = cast(Mock, answer_instance.graph_config.tooling.primary_llm)
    mock_llm.stream.return_value = [
        AIMessageChunk(content="Based on the search results, "),
        AIMessageChunk(content="the answer is abc[1]. "),
        AIMessageChunk(content="This is some other stuff."),
    ]

    # Force non-tool calling behavior
    answer_instance.graph_config.tooling.using_tool_calling_llm = False

    # Process the output
    output = list(answer_instance.processed_streamed_output)

    # Assertions
    assert len(output) == 8
    assert output[0] == ToolCallKickoff(
        tool_name="search", tool_args=DEFAULT_SEARCH_ARGS
    )
    assert output[1] == ToolResponse(
        id=SEARCH_DOC_CONTENT_ID,
        response=mock_contexts,
    )
    assert output[2] == ToolResponse(
        id=FINAL_CONTEXT_DOCUMENTS_ID,
        response=mock_search_results,
    )
    assert output[3] == ToolCallFinalResult(
        tool_name="search",
        tool_args=DEFAULT_SEARCH_ARGS,
        tool_result=[json.loads(doc.model_dump_json()) for doc in mock_search_results],
    )
    assert output[4] == OnyxAnswerPiece(answer_piece="Based on the search results, ")
    expected_citation = CitationInfo(citation_num=1, document_id="doc1")
    assert output[5] == expected_citation
    assert output[6] == OnyxAnswerPiece(
        answer_piece="the answer is abc[[1]](https://example.com/doc1). "
    )
    assert output[7] == OnyxAnswerPiece(answer_piece="This is some other stuff.")

    expected_answer = (
        "Based on the search results, "
        "the answer is abc[[1]](https://example.com/doc1). "
        "This is some other stuff."
    )
    assert answer_instance.llm_answer == expected_answer
    assert len(answer_instance.citations) == 1
    assert answer_instance.citations[0] == expected_citation

    # Verify LLM calls
    assert mock_llm.stream.call_count == 1
    call_args = mock_llm.stream.call_args

    # Verify that no tools were passed to the LLM
    assert "tools" not in call_args.kwargs or not call_args.kwargs["tools"]

    # Verify that the prompt was built correctly
    assert (
        call_args.kwargs["prompt"]
        == mock_search_tool.build_next_prompt.return_value.build.return_value
    )

    # Verify that get_args_for_non_tool_calling_llm was called on the mock_search_tool
    mock_search_tool.get_args_for_non_tool_calling_llm.assert_called_once_with(
        QUERY, [], answer_instance.graph_config.tooling.primary_llm
    )

    # Verify that the search tool's run method was called
    mock_search_tool.run.assert_called_once()


def test_is_cancelled(answer_instance: Answer) -> None:
    # Set up the LLM mock to return multiple chunks
    mock_llm = Mock()
    answer_instance.graph_config.tooling.primary_llm = mock_llm
    answer_instance.graph_config.tooling.fast_llm = mock_llm
    mock_llm.stream.return_value = [
        AIMessageChunk(content="This is the "),
        AIMessageChunk(content="first part."),
        AIMessageChunk(content="This should not be seen."),
    ]

    # Create a mutable object to control is_connected behavior
    connection_status = {"connected": True}
    answer_instance.is_connected = lambda: connection_status["connected"]

    # Process the output
    output = []
    for i, chunk in enumerate(answer_instance.processed_streamed_output):
        output.append(chunk)
        # Simulate disconnection after the second chunk
        if i == 1:
            connection_status["connected"] = False

    assert len(output) == 3
    assert output[0] == OnyxAnswerPiece(answer_piece="This is the ")
    assert output[1] == OnyxAnswerPiece(answer_piece="first part.")
    assert output[2] == StreamStopInfo(stop_reason=StreamStopReason.CANCELLED)

    # Verify that the stream was cancelled
    assert answer_instance.is_cancelled() is True

    # Verify that the final answer only contains the streamed parts
    assert answer_instance.llm_answer == "This is the first part."

    # Verify LLM calls
    mock_llm.stream.assert_called_once()
