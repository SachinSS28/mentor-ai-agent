"""The owner's tasks and timed reminders, each with a T-number ("done T3")."""

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, tzinfo

from sqlalchemy import or_, select, update

from mentor.db.models import Task
from mentor.db.session import get_sessionmaker

FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")
END_OF_DAY = time(23, 59)


def parse_when(text: str, tz: tzinfo, default_time: time = END_OF_DAY) -> datetime | None:
    """'2026-10-23 15:00' (or with a T) in the owner's time zone; a bare date gets
    `default_time`; empty -> None. Raises ValueError for anything else."""
    value = text.strip()
    if not value:
        return None
    for fmt in FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz)
        except ValueError:
            pass
    try:
        return datetime.combine(date.fromisoformat(value), default_time, tz)
    except ValueError:
        raise ValueError(f"{text!r} isn't a time like 2026-10-23 15:00") from None


def parse_ref(text: str) -> int | None:
    """'T3', 't3', '#3' or '3' -> 3; anything else (title words) -> None."""
    digits = text.strip().lstrip("#").lstrip("Tt")
    return int(digits) if digits.isdigit() else None


def add(
    title: str,
    now: datetime,
    due: datetime | None = None,
    remind_at: datetime | None = None,
    priority: int = 3,
    notes: str = "",
) -> Task:
    if not title.strip():
        raise ValueError("a task needs a title")
    if remind_at is not None and remind_at <= now:
        raise ValueError(f"the reminder time {remind_at:%a %d %b %H:%M} has already passed")
    task = Task(
        title=title.strip(),
        notes=notes.strip() or None,
        due=due,
        remind_at=remind_at,
        priority=max(1, min(priority, 4)),
        source="text",
        created_by="owner",
    )
    with get_sessionmaker()() as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


def find_open(text: str) -> list[Task]:
    """Open tasks by T-number, or whose title contains every word of `text`."""
    stmt = select(Task).where(Task.status == "open")
    ref = parse_ref(text)
    if ref is not None:
        stmt = stmt.where(Task.ref == ref)
    else:
        for word in text.split():
            stmt = stmt.where(Task.title.ilike(f"%{word}%"))
    with get_sessionmaker()() as session:
        return list(session.scalars(stmt.order_by(Task.ref)))


def close(ref: int, status: str, now: datetime) -> None:
    with get_sessionmaker()() as session:
        session.execute(
            update(Task)
            .where(Task.ref == ref)
            .values(status=status, completed_at=now if status == "done" else None)
        )
        session.commit()


def reminders_due(now: datetime) -> list[Task]:
    """Open tasks whose reminder time has come and that haven't been reminded yet."""
    query = (
        select(Task)
        .where(Task.status == "open", Task.remind_at <= now, Task.reminded_at.is_(None))
        .order_by(Task.remind_at)
    )
    with get_sessionmaker()() as session:
        return list(session.scalars(query))


def mark_reminded(refs: Sequence[int], now: datetime) -> None:
    with get_sessionmaker()() as session:
        session.execute(update(Task).where(Task.ref.in_(refs)).values(reminded_at=now))
        session.commit()


def done_between(start: datetime, end: datetime) -> list[Task]:
    query = select(Task).where(
        Task.status == "done", Task.completed_at >= start, Task.completed_at < end
    )
    with get_sessionmaker()() as session:
        return list(session.scalars(query.order_by(Task.completed_at)))


def overdue(now: datetime) -> list[Task]:
    query = select(Task).where(Task.status == "open", Task.due < now).order_by(Task.due)
    with get_sessionmaker()() as session:
        return list(session.scalars(query))


def due_soon(now: datetime, days: int = 7) -> list[Task]:
    """Open tasks due in the next `days` days, or with no due date (newest first)."""
    query = (
        select(Task)
        .where(
            Task.status == "open",
            or_(Task.due.is_(None), Task.due.between(now, now + timedelta(days=days))),
        )
        .order_by(Task.due.asc().nulls_last(), Task.priority, Task.ref.desc())
    )
    with get_sessionmaker()() as session:
        return list(session.scalars(query))
