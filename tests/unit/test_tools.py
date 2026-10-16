from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from mentor.agent.tools import format_task
from mentor.db.models import Task


def test_due_time_is_shown_in_the_owners_timezone() -> None:
    task = Task(
        title="Send GST working",
        priority=1,
        due=datetime(2026, 10, 23, 11, 30, tzinfo=UTC),
        workspace_id="consulting",
    )
    line = format_task(task, ZoneInfo("Asia/Kolkata"))
    assert line == "- Send GST working (due Fri 23 Oct 17:00, P1, consulting)"


def test_task_without_due_date_or_workspace() -> None:
    assert format_task(Task(title="Read", priority=3), ZoneInfo("Asia/Kolkata")) == "- Read (P3)"
