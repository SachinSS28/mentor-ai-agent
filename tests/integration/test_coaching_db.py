"""Tasks, reminders, the weekly review and research highlights on a throwaway database."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from tests.integration.conftest import NewDatabase

from mentor import review
from mentor.agent.tools import add_task, complete_task, list_open_tasks
from mentor.briefing import highlights
from mentor.briefing import store as briefing_store
from mentor.briefing.highlights import Highlight
from mentor.mcp_servers.mentor_core import tasks
from mentor.network import store as contacts
from mentor.network.experts import Candidate

HKT = ZoneInfo("Asia/Hong_Kong")
SUNDAY = datetime(2026, 10, 25, 19, 5, tzinfo=HKT)


def test_tasks_with_reminders_from_add_to_done(new_database: NewDatabase) -> None:
    new_database("mentor_test_tasks")
    now = datetime.now(HKT)
    soon = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    reply = add_task.invoke({"title": "Email Prof. Chan", "due": soon[:10], "remind": soon})
    assert reply.startswith("Added T1: Email Prof. Chan, due ")
    assert "reminder on Telegram" in reply
    assert add_task.invoke({"title": "Late", "remind": "2020-01-01 09:00"}).startswith(
        "Not added: the reminder time"
    )
    add_task.invoke({"title": "Email the WSD team"})
    assert "T1 Email Prof. Chan" in list_open_tasks.invoke({})

    assert complete_task.invoke({"task": "email"}).startswith("Several open tasks match")
    assert complete_task.invoke({"task": "WSD"}) == "T2 Email the WSD team: done."
    assert complete_task.invoke({"task": "T9"}) == "No open task matches 'T9'."

    later = now + timedelta(days=2)
    assert [t.ref for t in tasks.reminders_due(now)] == []
    assert [t.ref for t in tasks.reminders_due(later)] == [1]
    tasks.mark_reminded([1], later)
    assert tasks.reminders_due(later) == []  # each reminder goes once
    assert [t.title for t in tasks.done_between(now - timedelta(hours=1), later)] == [
        "Email the WSD team"
    ]


def test_highlights_are_sent_once(new_database: NewDatabase) -> None:
    new_database("mentor_test_highlights")
    paper = Highlight(
        "W1", "cited", "Leak detection", "https://doi.org/x", "Water Research", date(2025, 3, 1), 81
    )
    assert highlights.fresh([paper], 4, set()) == [paper]
    highlights.save_sent([paper], SUNDAY)
    highlights.save_sent([paper], SUNDAY)  # no duplicate
    assert highlights.fresh([paper], 4, set()) == []
    assert briefing_store.sent_between(SUNDAY - timedelta(days=1), SUNDAY + timedelta(days=1)) == 1


def test_weekly_review_lists_the_facts_and_the_coachs_note(
    new_database: NewDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_database("mentor_test_review")
    wednesday = SUNDAY - timedelta(days=4)
    done = tasks.add("Submit RGC proposal", wednesday)
    tasks.close(done.ref, "done", wednesday)
    tasks.add("Revise paper", wednesday, due=SUNDAY - timedelta(days=1))
    candidate = Candidate("A1", "Chan Tai Man", "PolyU")
    [person] = contacts.save_suggestions([candidate], date(2023, 1, 1), wednesday)
    contacts.track(f"P{person.ref}", "invited", "", "", 7, wednesday)

    monkeypatch.setattr(review, "coach_note", lambda facts: "Good week.\nTop three: ...")
    text = review.tick(SUNDAY)
    assert text is not None
    assert text.startswith("**Weekly review, week to Sun 25 Oct**")
    assert "• Tasks done: 1 (T1 Submit RGC proposal)" in text
    assert "• Overdue: T2 Revise paper (due Sat 24 Oct)" in text
    assert "• Job applications sent: 0" in text
    assert f"• Networking: invited: P{person.ref} Chan Tai Man" in text
    assert "• New people suggested to meet: 1" in text
    assert "• Uposatha next week: Mon 26 Oct (full moon)" in text
    assert "Good week." in text
    assert review.tick(SUNDAY + timedelta(minutes=15)) is None  # once a week
    assert review.tick(datetime(2026, 10, 26, 19, 5, tzinfo=HKT)) is None  # not Monday


def test_a_failing_coachs_note_still_sends_the_facts(
    new_database: NewDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_database("mentor_test_review_fallback")

    def broken(facts: object) -> str:
        raise RuntimeError("model down")

    monkeypatch.setattr(review, "coach_note", broken)
    text = review.message(SUNDAY)
    assert "• Tasks done: 0" in text
    assert "model down" not in text
    assert text.endswith("and I'll add them as tasks.")
