"""Google Calendar: read events, and add blocks to the owner's own calendar (never with guests)."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from mentor.mcp_servers.google_tools.auth import (
    CALENDAR_EVENTS,
    GoogleNotConnectedError,
    get_credentials,
    has_scope,
)

ADDED_BY = "Added by Mentor."


@dataclass(frozen=True)
class Event:
    title: str
    start: datetime | date  # a date means an all-day event
    end: datetime | date
    location: str | None
    attendees: int


def parse_event(item: dict[str, Any], tz: ZoneInfo) -> Event:
    def when(value: dict[str, str]) -> datetime | date:
        if "dateTime" in value:
            return datetime.fromisoformat(value["dateTime"]).astimezone(tz)
        return date.fromisoformat(value["date"])

    return Event(
        title=(item.get("summary") or "").strip() or "(no title)",
        start=when(item["start"]),
        end=when(item["end"]),
        location=item.get("location"),
        attendees=len(item.get("attendees", [])),
    )


def list_events(day: date, days: int, tz: ZoneInfo) -> list[Event]:
    """Events on the primary calendar from the start of `day` for `days` days."""
    start = datetime.combine(day, time.min, tz)
    service = build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)
    response = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=(start + timedelta(days=days)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        )
        .execute()
    )
    return [parse_event(item, tz) for item in response.get("items", [])]


def format_event(event: Event) -> str:
    start, end = event.start, event.end
    if isinstance(start, datetime) and isinstance(end, datetime):
        if start.date() == end.date():
            when = f"{start:%a %d %b %H:%M}–{end:%H:%M}"
        else:  # spans several days, e.g. started weeks ago and is still running
            when = f"{start:%a %d %b %H:%M} → {end:%a %d %b %H:%M}"
    else:
        last_day = end - timedelta(days=1)  # Google's all-day end date is exclusive
        if last_day <= start:
            when = f"{start:%a %d %b} (all day)"
        else:
            when = f"{start:%a %d %b} → {last_day:%a %d %b} (all day)"
    details = [
        d
        for d in (event.location, f"{event.attendees} attendees" if event.attendees else None)
        if d
    ]
    suffix = f" — {', '.join(details)}" if details else ""
    return f"- {when}: {event.title}{suffix}"


def block_body(title: str, start: datetime, end: datetime, notes: str = "") -> dict[str, Any]:
    """An event on the owner's own calendar only: no attendees, so nobody is ever invited."""
    zone = str(start.tzinfo)
    return {
        "summary": title.strip(),
        "description": f"{notes.strip()}\n\n{ADDED_BY}".strip(),
        "start": {"dateTime": start.isoformat(), "timeZone": zone},
        "end": {"dateTime": end.isoformat(), "timeZone": zone},
    }


def add_block(title: str, start: datetime, end: datetime, notes: str = "") -> str:
    """Adds the event to the primary calendar and returns its link."""
    creds = get_credentials()
    if not has_scope(creds, CALENDAR_EVENTS):
        raise GoogleNotConnectedError(
            "Mentor needs permission to add calendar events. Reconnect Google on the Mac with: "
            "uv run mentor google-auth"
        )
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    event = (
        service.events()
        .insert(calendarId="primary", body=block_body(title, start, end, notes), sendUpdates="none")
        .execute()
    )
    return str(event.get("htmlLink", ""))


def overlapping(events: list[Event], start: datetime, end: datetime) -> list[Event]:
    return [
        e
        for e in events
        if isinstance(e.start, datetime)
        and isinstance(e.end, datetime)
        and e.start < end
        and start < e.end
    ]
