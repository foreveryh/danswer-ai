from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph

from onyx.agents.agent_search.basic.states import BasicInput
from onyx.agents.agent_search.basic.states import BasicOutput
from onyx.agents.agent_search.basic.states import BasicState
from onyx.agents.agent_search.orchestration.nodes.basic_use_tool_response import (
    basic_use_tool_response,
)
from onyx.agents.agent_search.orchestration.nodes.llm_tool_choice import llm_tool_choice
from onyx.agents.agent_search.orchestration.nodes.prepare_tool_input import (
    prepare_tool_input,
)
from onyx.agents.agent_search.orchestration.nodes.tool_call import tool_call
from onyx.utils.logger import setup_logger

logger = setup_logger()


def basic_graph_builder() -> StateGraph:
    graph = StateGraph(
        state_schema=BasicState,
        input=BasicInput,
        output=BasicOutput,
    )

    ### Add nodes ###

    graph.add_node(
        node="prepare_tool_input",
        action=prepare_tool_input,
    )

    graph.add_node(
        node="llm_tool_choice",
        action=llm_tool_choice,
    )

    graph.add_node(
        node="tool_call",
        action=tool_call,
    )

    graph.add_node(
        node="basic_use_tool_response",
        action=basic_use_tool_response,
    )

    ### Add edges ###

    graph.add_edge(start_key=START, end_key="prepare_tool_input")

    graph.add_edge(start_key="prepare_tool_input", end_key="llm_tool_choice")

    graph.add_conditional_edges("llm_tool_choice", should_continue, ["tool_call", END])

    graph.add_edge(
        start_key="tool_call",
        end_key="basic_use_tool_response",
    )

    graph.add_edge(
        start_key="basic_use_tool_response",
        end_key=END,
    )

    return graph


def should_continue(state: BasicState) -> str:
    return (
        # If there are no tool calls, basic graph already streamed the answer
        END
        if state.tool_choice is None
        else "tool_call"
    )


if __name__ == "__main__":
    from onyx.db.engine import get_session_context_manager
    from onyx.context.search.models import SearchRequest
    from onyx.llm.factory import get_default_llms
    from onyx.agents.agent_search.shared_graph_utils.utils import get_test_config

    graph = basic_graph_builder()
    compiled_graph = graph.compile()
    input = BasicInput(_unused=True)
    primary_llm, fast_llm = get_default_llms()
    with get_session_context_manager() as db_session:
        config, _ = get_test_config(
            db_session=db_session,
            primary_llm=primary_llm,
            fast_llm=fast_llm,
            search_request=SearchRequest(query="How does onyx use FastAPI?"),
        )
        compiled_graph.invoke(input, config={"metadata": {"config": config}})
