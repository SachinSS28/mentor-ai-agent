"""The orchestrator graph: the model answers or calls tools until it can answer."""

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

type MentorGraph = CompiledStateGraph[MessagesState, None, MessagesState, MessagesState]

EMPTY_REPLY = "(The model returned an empty answer. Please ask again.)"

# Earlier conversation sent along with each message. The full thread stays in Postgres; this
# only bounds what each model call costs (and how long it takes) as a conversation grows.
HISTORY_TOKENS = 6000


def recent_history(
    messages: Sequence[BaseMessage], max_tokens: int = HISTORY_TOKENS
) -> list[BaseMessage]:
    """The current turn in full, plus as much earlier conversation as fits in max_tokens."""
    turn_starts = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    current = turn_starts[-1] if turn_starts else 0
    # Starting on a human message never leaves a tool result without the call that asked for it.
    earlier = trim_messages(
        messages[:current],
        max_tokens=max_tokens,
        token_counter=count_tokens_approximately,
        strategy="last",
        start_on="human",
    )
    return [*earlier, *messages[current:]]


def build_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: Callable[[], str],
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> MentorGraph:
    bound = model.bind_tools(tools)

    def agent(state: MessagesState) -> dict[str, list[BaseMessage]]:
        reply = bound.invoke([SystemMessage(system_prompt()), *recent_history(state["messages"])])
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


def ask(graph: MentorGraph, text: str, thread_id: str) -> str:
    """Send one message in a conversation thread and return Mentor's reply."""
    result = graph.invoke(
        {"messages": [HumanMessage(text)]}, {"configurable": {"thread_id": thread_id}}
    )
    reply = result["messages"][-1]
    text = reply.text if isinstance(reply, AIMessage) else str(reply.content)
    return text.strip() or EMPTY_REPLY
