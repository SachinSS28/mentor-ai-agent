"""The orchestrator graph: the model answers or calls tools until it can answer."""

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

type MentorGraph = CompiledStateGraph[MessagesState, None, MessagesState, MessagesState]

EMPTY_REPLY = "(The model returned an empty answer. Please ask again.)"


def build_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: Callable[[], str],
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> MentorGraph:
    bound = model.bind_tools(tools)

    def agent(state: MessagesState) -> dict[str, list[BaseMessage]]:
        reply = bound.invoke([SystemMessage(system_prompt()), *state["messages"]])
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
