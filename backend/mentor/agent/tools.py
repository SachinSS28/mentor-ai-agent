"""Read-only tools for the Phase 0 agent.

From Phase 1 these move into MCP servers (mentor-core, google-tools) behind Sentinel.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select

from mentor.charter import owner_timezone
from mentor.db.models import Task
from mentor.db.session import get_sessionmaker
from mentor.mcp_servers.google_tools import calendar, gmail
from mentor.mcp_servers.google_tools.auth import GoogleNotConnectedError


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


READ_ONLY_TOOLS: list[BaseTool] = [list_open_tasks, list_calendar_events, search_email]
