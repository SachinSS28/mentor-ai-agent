"""The orchestrator graph: the model answers or calls tools until it can answer."""

import re
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
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

# Tools that change something. A reply may say it did something only after one of these ran.
CHANGING_TOOLS = frozenset(
    {
        "add_task",
        "complete_task",
        "add_calendar_block",
        "draft_email",
        "remember",
        "forget_memory",
        "update_job",
        "track_contact",
    }
)
# "I've added", "I have saved", "has been scheduled" ...: an action reported as done.
CLAIMED_ACTION = re.compile(
    r"\b(?:I(?:'ve|’ve| have)(?: also| now| just)?|(?:has|have) been)\s+"
    r"(?:added|set(?: up)?|created|saved|scheduled|booked|put|drafted|updated|marked|"
    r"recorded|blocked|noted)\b",
    re.IGNORECASE,
)
HONESTY_CHECK = (
    "Check of your last reply: it says you did something, but no tool that changes anything ran "
    "in this turn, so nothing was done. Write the reply again without claiming any action: keep "
    "the rest, and offer to do it instead."
)
NOTHING_CHANGED = "(Nothing was changed yet: say the word and I'll do it.)"


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


def changed_this_turn(messages: Sequence[BaseMessage]) -> bool:
    """Whether a tool that changes something ran successfully since the owner's last message.
    Mentor's tools report failures starting with "Not" ("Not added: ...")."""
    turn_starts = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    for message in messages[turn_starts[-1] if turn_starts else 0 :]:
        if (
            isinstance(message, ToolMessage)
            and message.name in CHANGING_TOOLS
            and not message.text.startswith("Not ")
        ):
            return True
    return False


def claims_unbacked_action(reply: BaseMessage, messages: Sequence[BaseMessage]) -> bool:
    return bool(CLAIMED_ACTION.search(reply.text)) and not changed_this_turn(messages)


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
        prompt = [SystemMessage(system_prompt()), *history]
        reply = bound.invoke(prompt)
        final = isinstance(reply, AIMessage) and not reply.tool_calls
        if final and claims_unbacked_action(reply, state["messages"]):
            reply = honest(prompt, reply)
        return {"messages": [reply]}

    def honest(prompt: list[BaseMessage], reply: AIMessage) -> AIMessage:
        """Small models sometimes report an action they never took ("I've added a reminder").
        Ask once for the reply without the claim (the check isn't stored), and say plainly that
        nothing changed if the claim survives."""
        check = [*prompt, AIMessage(reply.text), HumanMessage(HONESTY_CHECK)]
        fixed = model.invoke(check).text.strip()
        if fixed and not CLAIMED_ACTION.search(fixed):
            return AIMessage(fixed)
        return AIMessage(f"{reply.text.strip()}\n\n{NOTHING_CHANGED}")

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
