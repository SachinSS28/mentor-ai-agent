"""Tools for the Phase 0 agent: read-only, except for Mentor's own long-term memory.

From Phase 1 these move into MCP servers (mentor-core, google-tools) behind Sentinel.
"""

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select

from mentor.agent.models import load_models_config
from mentor.charter import owner_timezone
from mentor.db.models import Memory, Task
from mentor.db.session import get_sessionmaker
from mentor.mcp_servers.google_tools import calendar, gmail
from mentor.mcp_servers.google_tools.auth import GoogleNotConnectedError
from mentor.mcp_servers.mentor_core import memory
from mentor.mcp_servers.mentor_core.memory import Layer


def format_task(task: Task, tz: ZoneInfo) -> str:
    details = [f"P{task.priority}"]
    if task.due is not None:
        details.insert(0, f"due {task.due.astimezone(tz):%a %d %b %H:%M}")
    if task.workspace_id:
        details.append(task.workspace_id)
    return f"- {task.title} ({', '.join(details)})"


def resolve_day(value: str, today: date) -> date:
    word = value.strip().lower()
    relative = {"": 0, "today": 0, "tomorrow": 1, "yesterday": -1}
    if word in relative:
        return today + timedelta(days=relative[word])
    return date.fromisoformat(word)


@tool
def list_open_tasks(limit: int = 10) -> str:
    """List the owner's open tasks, soonest due first.

    Use this for any question about tasks, to-dos, priorities or what is pending.
    """
    query = (
        select(Task)
        .where(Task.status == "open")
        .order_by(Task.due.asc().nulls_last(), Task.priority)
        .limit(max(1, min(limit, 50)))
    )
    with get_sessionmaker()() as session:
        tasks = session.scalars(query).all()
    if not tasks:
        return "No open tasks."
    tz = owner_timezone()
    return "\n".join(format_task(t, tz) for t in tasks)


@tool
def list_calendar_events(day: str = "today", days: int = 1) -> str:
    """List events on the owner's Google Calendar.

    Use this for any question about meetings, schedule, availability or the day ahead.
    day: "today", "tomorrow", "yesterday" or a date as YYYY-MM-DD.
    days: how many days to cover, starting at `day` (1 to 14).
    """
    tz = owner_timezone()
    start = resolve_day(day, datetime.now(tz).date())
    days = max(1, min(days, 14))
    try:
        events = calendar.list_events(start, days, tz)
    except GoogleNotConnectedError as error:
        return str(error)
    if not events:
        return f"No events on the calendar for {days} day(s) from {start:%a %d %b}."
    return "\n".join(calendar.format_event(e) for e in events)


@tool
def search_email(query: str, max_results: int = 5) -> str:
    """Search the owner's Gmail (read-only) and return sender, date, subject and a preview.

    query uses Gmail search syntax, e.g. "is:unread newer_than:2d" or "from:ravi subject:invoice".
    """
    try:
        emails = gmail.search_messages(query, max(1, min(max_results, 20)))
    except GoogleNotConnectedError as error:
        return str(error)
    return gmail.format_untrusted(emails)


def model_is_local() -> bool:
    """Private memories only go to a model running on this machine (Ollama), never a cloud one."""
    return load_models_config().roles["orchestrator"].provider == "ollama"


def format_memory(item: Memory, tz: ZoneInfo) -> str:
    private = ", private" if item.private_flag else ""
    saved = f"{item.updated_at.astimezone(tz):%d %b %Y}"
    return f"[{item.id}] ({item.layer}{private}, {saved}) {item.fact}"


@tool
def remember(fact: str, kind: Layer = "profile", replaces: str = "", private: bool = False) -> str:
    """Save a lasting fact about the owner to long-term memory, kept on the Mac and the cloud.

    Use it when the owner shares something worth keeping: their CV or background, preferences,
    people they mention, goals. Save the details in full, in the owner's words.
    kind: profile, preference, workspace, people, goal or episodic.
    replaces: the id of a saved memory this corrects (from recall_memories); it is overwritten.
    private: true only if the owner asks to keep it away from cloud models.
    """
    try:
        target = uuid.UUID(replaces) if replaces else None
        saved = memory.remember(fact, kind, private=private, replaces=target, source="chat")
    except (ValueError, LookupError) as error:
        return f"Not saved: {error}"
    return f"Saved to memory as {saved.id}."


@tool
def recall_memories(query: str | list[str] = "") -> str:
    """Look up what the owner told you before (long-term memory), with each memory's id.

    Use it before saying you don't know something about the owner, and when they ask what you
    remember. query: words to look for; leave empty to list everything.
    """
    # Small local models sometimes send a list of words (or [] for "everything") instead of text.
    words = " ".join(str(word) for word in query) if isinstance(query, list) else query
    found = memory.recall(words, include_private=model_is_local())
    if not found:
        return "Nothing saved in memory yet."
    tz = owner_timezone()
    return "\n".join(format_memory(item, tz) for item in found)


@tool
def forget_memory(memory_id: str) -> str:
    """Delete a saved memory on the Mac and the cloud, by its id from recall_memories."""
    try:
        forgotten = memory.forget(uuid.UUID(memory_id))
    except ValueError:
        forgotten = None
    return "Forgotten." if forgotten else f"There is no saved memory {memory_id}."


TOOLS: list[BaseTool] = [
    list_open_tasks,
    list_calendar_events,
    search_email,
    remember,
    recall_memories,
    forget_memory,
]
