"""The Google outbox, with the outbox, database and calendar stood in: what Mentor hands over,
what it does with what was sent, and when the bot still sends a reminder itself."""

import asyncio
import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import httpx
import pytest

from mentor import outbox
from mentor.db.models import Task
from mentor.settings import get_settings

HKT = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 11, 3, 21, 0, tzinfo=HKT)  # Tuesday evening
URL = "https://script.google.com/macros/s/AKfycbx-Mentor_123/exec"


def task(ref: int, title: str, remind: datetime | None = None, due: datetime | None = None) -> Task:
    return Task(ref=ref, title=title, remind_at=remind, due=due, status="open", priority=2)


class FakeOutbox:
    """Stands in for the Apps Script web app."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.sent: list[str] = []
        self.ok = True

    def __call__(self, url: str, json: dict[str, Any], **kwargs: Any) -> httpx.Response:
        self.requests.append(json)
        request = httpx.Request("POST", url)
        if not self.ok:
            raise httpx.ConnectError("no route", request=request)
        body = {"ok": True, "pending": len(json.get("messages", [])), "sent": self.sent}
        return httpx.Response(200, json=body, request=request)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[SimpleNamespace]:
    state: dict[str, str] = {}
    reminded: list[int] = []
    fake = FakeOutbox()
    reminders = [task(3, "Send the abstract", NOW + timedelta(hours=13))]
    monkeypatch.setenv("MENTOR_OUTBOX_PATH", str(tmp_path / "outbox.json"))
    monkeypatch.setenv("MENTOR_TELEGRAM_BOT_TOKEN", "123:secret-key")
    monkeypatch.setenv("MENTOR_TELEGRAM_ALLOWED_CHAT_ID", "42")
    get_settings.cache_clear()
    monkeypatch.setattr(outbox, "get_state", state.get)
    monkeypatch.setattr(outbox, "set_state", state.__setitem__)
    monkeypatch.setattr(outbox.httpx, "post", fake)
    monkeypatch.setattr(
        outbox.tasks,
        "upcoming_reminders",
        lambda start, until: [t for t in reminders if t.remind_at and start < t.remind_at <= until],
    )
    monkeypatch.setattr(outbox.tasks, "due_soon", lambda start, days=7: [])
    monkeypatch.setattr(outbox.tasks, "overdue", lambda start: [task(1, "Review the report")])
    monkeypatch.setattr(outbox.tasks, "mark_reminded", lambda refs, now: reminded.extend(refs))
    monkeypatch.setattr(outbox.calendar, "list_events", lambda day, days, tz: [])
    monkeypatch.setattr(outbox, "uposatha", lambda day, now: None)
    yield SimpleNamespace(
        state=state, fake=fake, reminders=reminders, reminded=reminded, path=tmp_path
    )
    get_settings.cache_clear()


def connect(world: SimpleNamespace) -> None:
    outbox.connect(URL)


def test_connecting_hands_over_the_bot_key_and_a_new_secret(world: SimpleNamespace) -> None:
    with pytest.raises(outbox.OutboxError, match="web app address"):
        outbox.connect("https://evil.example/exec")
    connect(world)
    first = world.fake.requests[0]
    assert (
        first["action"] == "connect"
        and first["token"] == "123:secret-key"  # noqa: S105 (a made-up test key)
        and first["chat_id"] == 42
    )
    assert len(first["secret"]) >= 32
    saved = world.path / "outbox.json"
    assert json.loads(saved.read_text()) == {"url": URL, "secret": first["secret"]}
    assert saved.stat().st_mode & 0o777 == 0o600


def test_the_plan_holds_reminders_and_tomorrows_morning(world: SimpleNamespace) -> None:
    planned = outbox.plan(NOW)
    assert [p.id for p in planned] == [
        f"task-3-{int(world.reminders[0].remind_at.timestamp())}",
        "morning-2026-11-04",  # tonight, only tomorrow's morning is still to come
    ]
    reminder, morning = planned
    assert reminder.text.startswith("⏰ **Reminder, T3:** Send the abstract")
    assert morning.at == datetime(2026, 11, 4, 8, 20, tzinfo=HKT)  # 20 minutes after 08:00
    assert "your plan for Wed 04 Nov" in morning.text
    assert "• T1 Review the report" in morning.text  # overdue
    assert "• 10:00 T3 Send the abstract" in morning.text  # tomorrow's reminder
    assert morning.payload()["at"] == int(morning.at.timestamp() * 1000)
    assert "<b>Overdue</b>" in morning.payload()["text"]  # as Telegram's HTML


def test_this_mornings_plan_is_dropped_once_the_live_message_went(world: SimpleNamespace) -> None:
    early = NOW.replace(hour=7, minute=0)
    assert "morning-2026-11-03" in [p.id for p in outbox.plan(early)]
    world.state["jobs.digest_sent_on"] = "2026-11-03"
    assert "morning-2026-11-03" not in [p.id for p in outbox.plan(early)]


def test_sync_marks_delivered_reminders_and_tells_the_conversation_once(
    world: SimpleNamespace,
) -> None:
    assert outbox.sync(NOW) is False  # not connected yet: nothing to do
    connect(world)
    assert outbox.sync(NOW) is True
    reminder_id = f"task-3-{int(world.reminders[0].remind_at.timestamp())}"
    assert reminder_id in outbox.handed_over()
    assert world.fake.requests[-1]["action"] == "plan"

    world.fake.sent = [reminder_id]  # the outbox sent it while the Mac was off
    told: list[str] = []
    later = NOW + timedelta(hours=14)
    assert outbox.sync(later, on_delivered=told.append)
    assert world.reminded == [3]
    assert told == ['⏰ **Reminder, T3:** Send the abstract\nSay "done T3" when it\'s done.']
    outbox.sync(later + timedelta(minutes=15), on_delivered=told.append)
    assert len(told) == 1 and world.reminded == [3]  # only once


def test_the_bot_sends_what_the_outbox_doesnt_hold(world: SimpleNamespace) -> None:
    reminder = world.reminders[0]
    assert outbox.bot_sends(reminder, NOW) is True  # no outbox: the bot sends, as before
    connect(world)
    outbox.sync(NOW)
    due = reminder.remind_at
    assert outbox.bot_sends(reminder, due + timedelta(minutes=1)) is False  # the outbox sends it
    assert outbox.bot_sends(reminder, due + timedelta(minutes=31)) is True  # unconfirmed: send it
    new = task(9, "Call the plumber", NOW + timedelta(hours=1))
    assert outbox.bot_sends(new, new.remind_at) is True  # never handed over


def test_new_reminders_mean_a_sync_and_a_dead_outbox_changes_nothing(
    world: SimpleNamespace,
) -> None:
    connect(world)
    outbox.sync(NOW)
    assert outbox.changed(NOW) is False
    world.reminders.append(task(9, "Call the plumber", NOW + timedelta(hours=1)))
    assert outbox.changed(NOW) is True
    held = outbox.handed_over()
    world.fake.ok = False
    assert outbox.sync(NOW) is False
    assert outbox.handed_over() == held  # still what the outbox last accepted


def test_the_bot_leaves_reminders_to_the_outbox(monkeypatch: pytest.MonkeyPatch) -> None:
    from mentor.channels.telegram import bot

    due = task(3, "Send the abstract", NOW)
    told = AsyncMock()
    monkeypatch.setattr(bot.tasks, "reminders_due", lambda now: [due])
    monkeypatch.setattr(bot.tasks, "mark_reminded", Mock())
    monkeypatch.setattr(bot.outbox, "bot_sends", lambda task, now: False)
    monkeypatch.setattr(bot.outbox, "config", lambda: None)
    monkeypatch.setattr(bot, "tell_owner", told)
    asyncio.run(bot.make_reminders(Mock(), 1)(Mock()))
    told.assert_not_called()


def test_a_test_message_survives_the_bots_next_plan_until_sent(world: SimpleNamespace) -> None:
    connect(world)
    test = outbox.test_message(NOW)
    outbox.sync(NOW, [test])
    outbox.sync(NOW + timedelta(seconds=30))  # the bot's own plan, before the test went out
    assert test.id in [m["id"] for m in world.fake.requests[-1]["messages"]]
    world.fake.sent = [test.id]
    outbox.sync(NOW + timedelta(minutes=2))
    outbox.sync(NOW + timedelta(minutes=3))
    assert test.id not in [m["id"] for m in world.fake.requests[-1]["messages"]]


def test_an_unsent_test_message_is_given_up_after_half_an_hour(world: SimpleNamespace) -> None:
    connect(world)
    test = outbox.test_message(NOW)
    outbox.sync(NOW, [test])
    outbox.sync(test.at + outbox.GIVE_UP + timedelta(minutes=1))
    assert test.id not in [m["id"] for m in world.fake.requests[-1]["messages"]]


def test_a_test_message_comes_a_minute_later() -> None:
    message = outbox.test_message(NOW)
    assert message.at == NOW + timedelta(minutes=1) and message.id == f"test-{int(NOW.timestamp())}"
    assert date(2026, 11, 3) == message.at.date()
