from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from mentor.agent.tools import format_task
from mentor.db.models import Task


def test_due_time_is_shown_in_the_owners_timezone() -> None:
    task = Task(
        ref=3,
        title="Send quarterly report",
        priority=1,
        due=datetime(2026, 10, 23, 11, 30, tzinfo=UTC),
        workspace_id="consulting",
    )
    line = format_task(task, ZoneInfo("Asia/Hong_Kong"))
    assert line == "- T3 Send quarterly report (due Fri 23 Oct 19:30, P1, consulting)"


def test_task_without_due_date_or_workspace() -> None:
    task = Task(ref=4, title="Read", priority=3, notes="chapter 2")
    assert format_task(task, ZoneInfo("Asia/Hong_Kong")) == "- T4 Read (P3) — chapter 2"


def test_pending_reminder_is_shown_until_it_has_gone_out() -> None:
    remind = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
    task = Task(ref=5, title="Call WSD", priority=2, remind_at=remind)
    hk = ZoneInfo("Asia/Hong_Kong")
    assert format_task(task, hk) == "- T5 Call WSD (P2, reminder Fri 23 Oct 09:00)"
    task.reminded_at = remind
    assert format_task(task, hk) == "- T5 Call WSD (P2)"
