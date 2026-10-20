from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from mentor import checkin
from mentor.charter import CheckinConfig
from mentor.db.models import Task
from mentor.mcp_servers.google_tools import calendar
from mentor.mcp_servers.google_tools.auth import GoogleNotConnectedError

HKT = ZoneInfo("Asia/Hong_Kong")
CONFIG = CheckinConfig()


def hk(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 21, hour, minute, tzinfo=HKT)


def test_check_ins_every_two_hours_in_the_day() -> None:
    assert [s.hour for s in checkin.slots(hk(9), CONFIG)] == [10, 12, 14, 16, 18, 20]


def test_a_check_in_is_due_once_per_slot_and_not_long_after() -> None:
    assert checkin.due_slot(hk(9, 59), CONFIG, None) is None  # before the first
    assert checkin.due_slot(hk(14, 1), CONFIG, None) == hk(14)
    assert checkin.due_slot(hk(14, 1), CONFIG, "2026-10-21T14:00") is None  # already handled
    assert checkin.due_slot(hk(15, 30), CONFIG, None) is None  # missed while the Mac slept
    assert checkin.due_slot(hk(23), CONFIG, None) is None  # quiet at night


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    saved: dict[str, str] = {}
    monkeypatch.setattr(checkin, "get_state", saved.get)
    monkeypatch.setattr(checkin, "set_state", saved.__setitem__)
    return saved


@pytest.fixture
def records(monkeypatch: pytest.MonkeyPatch) -> None:
    late = Task(ref=2, title="Revise paper", due=datetime(2026, 10, 20, 18, 0, tzinfo=HKT))
    today = Task(ref=5, title="Send RGC budget", due=hk(17))
    later = Task(ref=7, title="Read Nature Water issue")
    monkeypatch.setattr(checkin.tasks, "overdue", lambda now: [late])
    monkeypatch.setattr(checkin.tasks, "due_soon", lambda now: [today, later])
    meeting = calendar.Event("Lab meeting", hk(15), hk(16), None, 4)
    done = calendar.Event("Breakfast", hk(8), hk(9), None, 0)
    holiday = calendar.Event("Holiday", date(2026, 10, 21), date(2026, 10, 22), None, 0)
    monkeypatch.setattr(checkin.calendar, "list_events", lambda d, n, tz: [done, meeting, holiday])


def test_facts_cover_tasks_and_the_rest_of_the_day(records: None) -> None:
    assert checkin.facts(hk(14)) == [
        "Overdue: T2 Revise paper (due Tue 20 Oct 18:00)",
        "Due today: T5 Send RGC budget (due Wed 21 Oct 17:00)",
        "Other open tasks: T7 Read Nature Water issue",
        "Still on the calendar today: Wed 21 Oct 15:00–16:00: Lab meeting — 4 attendees",
    ]


def test_facts_without_google_or_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checkin.tasks, "overdue", lambda now: [])
    monkeypatch.setattr(checkin.tasks, "due_soon", lambda now: [])

    def offline(*args: Any) -> list[calendar.Event]:
        raise GoogleNotConnectedError("expired")

    monkeypatch.setattr(checkin.calendar, "list_events", offline)
    assert checkin.facts(hk(14)) == ["No open tasks on the list"]


def test_check_in_rotates_focus_and_skips_while_chatting(
    state: dict[str, str], records: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    focuses: list[int] = []

    def compose(now: datetime, focus: int, found: list[str]) -> str:
        focuses.append(focus)
        return "The RGC budget is due at 17:00. What's in the way?"

    monkeypatch.setattr(checkin, "compose", compose)
    text = checkin.tick(hk(12, 1))
    assert text == "**Check-in, 12:01**\nThe RGC budget is due at 17:00. What's in the way?"
    assert checkin.tick(hk(12, 2)) is None  # once per slot

    state[checkin.OWNER_LAST_MESSAGE] = hk(13, 40).isoformat()
    assert checkin.tick(hk(14, 1)) is None  # the owner is talking already
    assert state[checkin.LAST_SLOT] == "2026-10-21T14:00"  # and it isn't sent later
    assert checkin.tick(hk(16, 1)) is not None
    assert focuses == [1, 3]  # 12:00 and 16:00 are the 2nd and 4th check-ins


def test_a_failed_or_boastful_check_in_falls_back_to_the_facts(
    records: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Boastful:
        def invoke(self, messages: Any) -> Any:
            return type("Reply", (), {"text": "I've added a reminder for the budget."})()

    monkeypatch.setattr(checkin, "get_chat_model", lambda role: Boastful())
    monkeypatch.setattr(checkin, "mentor_system_prompt", lambda: "You are Mentor.")
    text = checkin.message(hk(14), 0)
    assert text.startswith("**Check-in, 14:00**\n• Overdue: T2 Revise paper")
    assert text.endswith("How is it going? What can I take on?")
    assert "I've added" not in text
