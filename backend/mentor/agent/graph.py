"""The orchestrator graph: the model answers or calls tools until it can answer."""

import re
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from typing import Any, cast
from urllib.parse import unquote
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from mentor import word
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
        "make_word_document",
        "make_cv",
        "remember",
        "forget_memory",
        "update_job",
        "track_contact",
        "open_in_safari",
    }
)
# "I've added", "I have saved", "has been scheduled" ...: an action reported as done.
CLAIMED_ACTION = re.compile(
    r"\b(?:I(?:'ve|’ve| have)(?: also| now| just)?|(?:has|have) been)\s+"
    r"(?:added|set(?: up)?|created|saved|scheduled|booked|put|drafted|updated|marked|"
    r"recorded|blocked|noted)\b",
    re.IGNORECASE,
)
# "I've also drafted a cover letter": only a saved Word file or Gmail draft backs that, whatever
# else changed this turn (making a CV doesn't).
CLAIMED_LETTER = re.compile(
    r"\bI(?:'ve|’ve| have)(?: also| now| just)?\s+(?:drafted|written|prepared|saved|created)\s+"
    r"(?:a |an |the |your )?(?:\w+ ){0,2}(?:cover )?letter",
    re.IGNORECASE,
)
LETTER_TOOLS = frozenset({"make_word_document", "draft_email"})
FILE_TOOLS = frozenset({"make_word_document", "make_cv"})  # they reply with the saved file's link
# Things no tool of Mentor's does (it never sends, connects, applies or messages for the owner),
# so a reply saying it did one is wrong whatever else ran this turn.
NEVER_DONE = re.compile(
    r"\bI(?:'ve|’ve| have)(?: also| now| just)?\s+"
    r"(?:sent|submitted|connected|applied|messaged|invited)\b"
    r"|\b(?:connection request|invitation|application|message)s?\s+(?:has|have)\s+been\s+"
    r"(?:sent|submitted)\b",
    re.IGNORECASE,
)
HONESTY_CHECK = (
    "Check of your last reply: it says you did something that no tool did in this turn (and you "
    "never send, connect, apply or message for the owner), so it wasn't done. Write the reply "
    "again without claiming it: keep the rest, and offer to do or prepare it instead. Write to "
    "the owner directly; don't mention this check or apologise."
)
NOTHING_CHANGED = "(Nothing was changed yet: say the word and I'll do it.)"

# Words in the names of the errors a lost connection raises, across the libraries the tools use
# (httpx, requests, urllib3, httplib2 for Google, google-auth, sockets).
NETWORK_ERRORS = (
    "ServerNotFound",
    "ConnectionError",
    "ConnectError",
    "TransportError",
    "NameResolution",
    "gaierror",
    "Timeout",
    "MaxRetryError",
)
NO_CONNECTION = (
    "No internet connection right now, so this couldn't be reached. Say so in one line and "
    "answer from what you have."
)
NOT_MINE_TO_DO = "(That part wasn't done: I never send, connect, apply or message for you.)"

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
# "... as a Word file", "a .docx", "in Word": the owner wants a document saved, not just written.
WORD_REQUEST = re.compile(
    r"\bword\s+(?:file|document|doc)\b|\.?docx\b|\bin (?:ms )?word\b|\bms word\b", re.IGNORECASE
)
WORD_NUDGE = (
    "Check of your last reply: the owner asked for a Word file, but you didn't save one. Call "
    "make_word_document now with a short title and the document's text alone (no preamble), "
    "so the owner gets the file."
)
# "Prepare my CV for J12", "tailor my resume": the owner wants the formatted CV, made by make_cv.
CV_REQUEST = re.compile(
    r"\b(?:cv|c\.v\.|résumé|curriculum vitae)s?\b|\b(?:my|a|the|new)\s+resumes?\b", re.IGNORECASE
)
MAKING = re.compile(
    r"\b(?:make|prepare|create|write|draft|tailor|update|build|generate|redo|format|need|want)",
    re.IGNORECASE,
)
CV_NUDGE = (
    "Check of your last reply: the owner asked for a CV, but you didn't make one. Call make_cv "
    "now (job: the J-number, or the employer and job title, if there is one), so the owner gets "
    "the formatted Word file."
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


def changed_this_turn(
    messages: Sequence[BaseMessage], tools: frozenset[str] = CHANGING_TOOLS
) -> bool:
    """Whether a tool that changes something (one of `tools`) ran successfully since the owner's
    last message. Mentor's tools report failures starting with "Not" ("Not added: ..."); a tool
    that raised is marked as an error."""
    return any(
        isinstance(message, ToolMessage)
        and message.name in tools
        and message.status != "error"
        and not message.text.startswith("Not ")
        for message in this_turn(messages)
    )


def lost_connection(error: BaseException) -> bool:
    """Whether an error, or one that caused it, comes from a lost internet connection."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names = [cls.__name__ for cls in type(current).__mro__]
        if any(word in name for name in names for word in NETWORK_ERRORS):
            return True
        current = current.__cause__ or current.__context__
    return False


def tool_failed(error: Exception) -> str:
    """A tool that raises tells the model why, and the turn goes on instead of ending in an
    error (e.g. the calendar while the Mac has no internet)."""
    if lost_connection(error):
        return NO_CONNECTION
    return f"This didn't work ({type(error).__name__}: {str(error)[:200]}). Say so in one line."


def period_asked(question: str) -> str:
    """The my_activity period a question is about."""
    if hours := re.search(r"last (\d+) hours?", question, re.IGNORECASE):
        return f"last {hours.group(1)} hours"
    for period in ("yesterday", "week"):
        if period in question.lower():
            return period
    return "today"


def file_missing(messages: Sequence[BaseMessage], tools: set[str]) -> str | None:
    """The reminder to send when the owner asked for a CV or a Word file this turn and none was
    saved; None otherwise."""
    turn = this_turn(messages)
    if not turn or not isinstance(turn[0], HumanMessage):
        return None
    asked = turn[0].text
    saved = {m.name for m in turn if isinstance(m, ToolMessage)}
    if "make_cv" in tools and CV_REQUEST.search(asked) and MAKING.search(asked):
        return None if "make_cv" in saved else CV_NUDGE
    if "make_word_document" in tools and WORD_REQUEST.search(asked):
        return None if saved & {"make_word_document", "make_cv"} else WORD_NUDGE
    return None


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


def links_left_out(reply: BaseMessage, messages: Sequence[BaseMessage]) -> list[str]:
    """Links to files saved this turn that the reply leaves out (small models sometimes name the
    file instead): without the link, the chat page has nothing to download and Telegram nothing
    to send."""
    named = set(word.linked_files(reply.text))
    return [
        link.group(0)
        for message in this_turn(messages)
        if isinstance(message, ToolMessage) and message.name in FILE_TOOLS
        for link in word.FILE_LINK.finditer(message.text)
        if unquote(link.group(2)) not in named
    ]


def claims_unbacked_action(reply: BaseMessage, messages: Sequence[BaseMessage]) -> bool:
    if NEVER_DONE.search(reply.text):
        return True
    if CLAIMED_LETTER.search(reply.text) and not changed_this_turn(messages, LETTER_TOOLS):
        return True
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
    writes_files = {t.name for t in tools} & {"make_word_document", "make_cv"}

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
        if final and writes_files and (nudge := file_missing(messages, writes_files)):
            # Small models often paste the document into the reply instead of saving it: ask
            # once (the request isn't kept) for the tool call that saves it.
            saved = bound.invoke([*prompt, AIMessage(reply.text), HumanMessage(nudge)])
            if isinstance(saved, AIMessage) and saved.tool_calls:
                return {"messages": [saved]}
        if final and claims_unbacked_action(reply, messages):
            reply = honest(prompt, reply, messages)
        if final and (links := links_left_out(reply, messages)):
            reply = AIMessage(f"{reply.text.rstrip()}\n\n" + "\n".join(links))
        return {"messages": [reply]}

    def honest(
        prompt: list[BaseMessage], reply: AIMessage, messages: Sequence[BaseMessage]
    ) -> AIMessage:
        """Small models sometimes report an action they never took ("I've added a reminder").
        Ask once for the reply without the claim (the check isn't stored), and say plainly that
        it wasn't done if the claim survives."""
        check = [*prompt, AIMessage(reply.text), HumanMessage(HONESTY_CHECK)]
        fixed = model.invoke(check).text.strip()
        if fixed and not claims_unbacked_action(AIMessage(fixed), messages):
            return AIMessage(fixed)
        note = NOT_MINE_TO_DO if NEVER_DONE.search(reply.text) else NOTHING_CHANGED
        return AIMessage(f"{reply.text.strip()}\n\n{note}")

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=tool_failed))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


def reply_text(reply: BaseMessage) -> str:
    """What the owner sees of Mentor's final message."""
    if reply.response_metadata.get("stop_reason") == "refusal":
        return REFUSAL_REPLY
    text = reply.text if isinstance(reply, AIMessage) else str(reply.content)
    return text.strip() or EMPTY_REPLY


def ask(graph: MentorGraph, text: str, thread_id: str, sent_at: datetime | None = None) -> str:
    """Send one message in a conversation thread and return Mentor's reply."""
    sent_at = sent_at or datetime.now(owner_timezone())
    result = graph.invoke(
        {"messages": [HumanMessage(stamp(text, sent_at))]},
        {"configurable": {"thread_id": thread_id}},
    )
    return reply_text(result["messages"][-1])


def stream_ask(
    graph: MentorGraph, text: str, thread_id: str, sent_at: datetime | None = None
) -> Iterator[tuple[str, str]]:
    """Like ask, while Mentor works: ("tool", name) when a tool starts, ("token", text) as the
    answer is written, then ("final", reply). The final reply is the one to keep: the honesty
    and activity checks can still replace what was streamed."""
    sent_at = sent_at or datetime.now(owner_timezone())
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    started: set[str] = set()
    for event in graph.stream(
        {"messages": [HumanMessage(stamp(text, sent_at))]}, config, stream_mode="messages"
    ):
        item, meta = cast(tuple[BaseMessage, dict[str, Any]], event)
        if meta.get("langgraph_node") != "agent" or not isinstance(item, AIMessage):
            continue
        if isinstance(item, AIMessageChunk):
            calls = [chunk["name"] or "" for chunk in item.tool_call_chunks]
        else:
            calls = [call["name"] for call in item.tool_calls]
        for name in calls:
            if name and name not in started:
                started.add(name)
                yield "tool", name
        if not calls and isinstance(item, AIMessageChunk) and item.text:
            yield "token", item.text
    yield "final", reply_text(graph.get_state(config).values["messages"][-1])
