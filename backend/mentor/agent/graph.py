"""The orchestrator graph: the model answers or calls tools until it can answer."""

import re
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

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

# Small models answer "can you see my screen?" from what they believe about themselves ("No"),
# or describe activity they never looked up. Such questions, and replies that talk about the
# activity journal without having read it this turn, go to the journal first.
ACTIVITY_TOOLS = frozenset({"my_activity", "pause_watching"})
SCREEN_QUESTION = re.compile(
    r"\b(?:see|look at|watch(?:ing)?|view)\s+(?:my|the)\s+"
    r"(?:screen|mac|computer|desktop|laptop)\b"
    r"|\bwhat\s+(?:am|was)\s+i\s+(?:doing|working on|looking at)\b"
    r"|\bwhat\s+(?:did|have)\s+i\s+(?:been\s+)?(?:do(?:ne|ing)?|work(?:ed|ing)?\s+on)\b",
    re.IGNORECASE,
)
# "The activity journal shows ...", "according to your journal": activity reported as looked up.
CLAIMED_ACTIVITY = re.compile(
    r"\bjournal\s+(?:shows|says|records|indicates|tells)\b"
    r"|\b(?:according to|based on|from)\s+(?:the|your)\s+(?:activity\s+)?journal\b",
    re.IGNORECASE,
)


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


def this_turn(messages: Sequence[BaseMessage]) -> Sequence[BaseMessage]:
    """The messages since the owner's last message, including it."""
    turn_starts = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    return messages[turn_starts[-1] if turn_starts else 0 :]


def changed_this_turn(messages: Sequence[BaseMessage]) -> bool:
    """Whether a tool that changes something ran successfully since the owner's last message.
    Mentor's tools report failures starting with "Not" ("Not added: ...")."""
    return any(
        isinstance(message, ToolMessage)
        and message.name in CHANGING_TOOLS
        and not message.text.startswith("Not ")
        for message in this_turn(messages)
    )


def period_asked(question: str) -> str:
    """The my_activity period a question is about."""
    if hours := re.search(r"last (\d+) hours?", question, re.IGNORECASE):
        return f"last {hours.group(1)} hours"
    for word in ("yesterday", "week"):
        if word in question.lower():
            return word
    return "today"


def activity_lookup(messages: Sequence[BaseMessage], reply: BaseMessage | None) -> AIMessage | None:
    """A my_activity call to make before answering: for a question about the screen (reply is
    None, the model hasn't been asked yet), or for a reply that describes the owner's activity
    without having read the journal this turn. None when no lookup is needed."""
    turn = this_turn(messages)
    if any(isinstance(m, ToolMessage) and m.name in ACTIVITY_TOOLS for m in turn):
        return None
    question = turn[0].text if turn and isinstance(turn[0], HumanMessage) else ""
    if reply is None and not SCREEN_QUESTION.search(question):
        return None
    if reply is not None and not CLAIMED_ACTIVITY.search(reply.text):
        return None
    lookup = {"name": "my_activity", "args": {"period": period_asked(question)}}
    return AIMessage("", tool_calls=[{**lookup, "id": f"look-{uuid4().hex[:12]}"}])


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
    reads_activity = "my_activity" in {t.name for t in tools}

    def agent(state: MessagesState) -> dict[str, list[BaseMessage]]:
        messages = state["messages"]
        starting = bool(messages) and isinstance(messages[-1], HumanMessage)
        if reads_activity and starting and (lookup := activity_lookup(messages, None)):
            return {"messages": [lookup]}
        history = recent_history(messages)
        if plain_text_history:
            history = [as_plain_text(m) for m in history]
        prompt = [SystemMessage(system_prompt()), *history]
        reply = bound.invoke(prompt)
        final = isinstance(reply, AIMessage) and not reply.tool_calls
        if final and reads_activity and (lookup := activity_lookup(messages, reply)):
            return {"messages": [lookup]}  # the made-up description is dropped
        if final and claims_unbacked_action(reply, messages):
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
