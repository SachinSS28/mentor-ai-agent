"""Meeting Secretary: pre-meeting briefs before upcoming calendar events."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from mentor.charter import owner_timezone
from mentor.db.models import Meeting, Task
from mentor.db.session import get_sessionmaker
from mentor.mcp_servers.google_tools import calendar
from mentor.mcp_servers.google_tools.auth import GoogleNotConnectedError

log = logging.getLogger(__name__)

# Events starting between 10 and 45 minutes from now get a pre-meeting brief.
LOOKAHEAD_MINUTES_MIN = 10
LOOKAHEAD_MINUTES_MAX = 45


def upcoming_meetings_to_brief(now: datetime) -> list[calendar.Event]:
    """Finds events on Google Calendar that need a pre-meeting briefing."""
    tz = owner_timezone()
    try:
        events = calendar.list_events(now.date(), 2, tz)
    except (GoogleNotConnectedError, Exception) as error:
        log.debug("Calendar unavailable for meeting briefing: %s", error)
        return []

    min_start = now + timedelta(minutes=LOOKAHEAD_MINUTES_MIN)
    max_start = now + timedelta(minutes=LOOKAHEAD_MINUTES_MAX)

    due: list[calendar.Event] = []
    for event in events:
        starts_soon = isinstance(event.start, datetime) and min_start <= event.start <= max_start
        if starts_soon and not is_briefed(event.id or event.title):
            due.append(event)
    return due


def is_briefed(event_id: str) -> bool:
    with get_sessionmaker()() as session:
        found = session.scalar(select(Meeting).where(Meeting.calendar_event_id == event_id))
        return found is not None and bool(found.brief)


def record_brief(event: calendar.Event, brief_text: str) -> None:
    event_id = event.id or event.title
    starts = event.start if isinstance(event.start, datetime) else datetime.now(owner_timezone())
    with get_sessionmaker()() as session, session.begin():
        found = session.scalar(select(Meeting).where(Meeting.calendar_event_id == event_id))
        if found is None:
            found = Meeting(
                calendar_event_id=event_id,
                title=event.title,
                starts_at=starts,
                brief=brief_text,
            )
            session.add(found)
        else:
            found.brief = brief_text


def format_brief(event: calendar.Event, now: datetime) -> str:
    starts_str = (
        event.start.strftime("%H:%M") if isinstance(event.start, datetime) else str(event.start)
    )
    minutes_left = (
        int((event.start - now).total_seconds() / 60) if isinstance(event.start, datetime) else 30
    )

    lines: list[str] = [
        f"🗓 **Pre-Meeting Brief: {event.title}**",
        f"⏰ Starts in **{minutes_left} minutes** ({starts_str} HKT)",
    ]
    if event.location:
        lines.append(f"📍 **Location:** {event.location}")
    if event.attendees:
        lines.append(f"👥 **Attendees:** {event.attendees} participant(s)")

    # Relevant tasks from the database
    with get_sessionmaker()() as session:
        related_tasks = session.scalars(select(Task).where(Task.status == "open").limit(3)).all()
        if related_tasks:
            lines.append("📋 **Active Tasks to keep in mind:**")
            for t in related_tasks:
                lines.append(f"- T{t.ref} {t.title}")

    if event.description:
        desc = event.description.strip()
        if len(desc) > 300:
            desc = desc[:297] + "..."
        lines.append(f"📝 **Agenda:**\n{desc}")

    return "\n".join(lines)


def tick(now: datetime) -> list[str]:
    """Generates pre-meeting briefs for meetings about to start."""
    due = upcoming_meetings_to_brief(now)
    briefs: list[str] = []
    for event in due:
        try:
            brief = format_brief(event, now)
            record_brief(event, brief)
            briefs.append(brief)
        except Exception:
            log.exception("Failed to build brief for %s", event.title)
    return briefs
