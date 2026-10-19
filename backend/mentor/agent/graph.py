"""The orchestrator graph: the model answers or calls tools until it can answer."""

from collections.abc import Callable, Sequence
from datetime import datetime
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

from mentor.agent.prompts import stamp
from mentor.charter import owner_timezone

type MentorGraph = CompiledStateGraph[MessagesState, None, MessagesState, MessagesState]

EMPTY_REPLY = "(The model returned an empty answer. Please ask again.)"
REFUSAL_REPLY = "(Claude declined to answer that. Try rephrasing it.)"

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


def as_plain_text(message: BaseMessage) -> BaseMessage:
    """An assistant turn with only its text, for models that can't read Claude's own blocks
    (thinking, fallback markers). Tool calls are kept: they live outside the content."""
    if not isinstance(message, AIMessage) or isinstance(message.content, str):
        return message
    return message.model_copy(update={"content": message.text})


def build_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: Callable[[], str],
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    plain_text_history: bool = False,
) -> MentorGraph:
    """plain_text_history: send earlier assistant turns as text only (non-Claude models). The
    stored conversation is never changed, so Claude still gets its turns back unmodified."""
    bound = model.bind_tools(tools)

    def agent(state: MessagesState) -> dict[str, list[BaseMessage]]:
        history = recent_history(state["messages"])
        if plain_text_history:
            history = [as_plain_text(m) for m in history]
        reply = bound.invoke([SystemMessage(system_prompt()), *history])
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


def ask(graph: MentorGraph, text: str, thread_id: str, sent_at: datetime | None = None) -> str:
    """Send one message in a conversation thread and return Mentor's reply."""
    sent_at = sent_at or datetime.now(owner_timezone())
    result = graph.invoke(
        {"messages": [HumanMessage(stamp(text, sent_at))]},
        {"configurable": {"thread_id": thread_id}},
    )
    reply = result["messages"][-1]
    if reply.response_metadata.get("stop_reason") == "refusal":
        return REFUSAL_REPLY
    text = reply.text if isinstance(reply, AIMessage) else str(reply.content)
    return text.strip() or EMPTY_REPLY
