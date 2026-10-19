"""The Job Desk end to end on a throwaway database, with Gmail and the model stood in."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from tests.helpers_jobs import ALERT, ALERT_2, FakeRater
from tests.integration.conftest import NewDatabase

from mentor.agent.tools import draft_email, find_jobs, update_job
from mentor.jobs import desk, store
from mentor.mcp_servers.google_tools.auth import GoogleNotConnectedError

HKT = ZoneInfo("Asia/Hong_Kong")
EMAILS = {"e1": ALERT, "e2": ALERT_2}


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> None:
    received = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
    monkeypatch.setattr(desk.gmail, "search_ids", lambda query, n: list(EMAILS))
    monkeypatch.setattr(desk.gmail, "message_text", lambda email_id: (EMAILS[email_id], received))


def score_everything() -> None:
    desk.score_jobs(FakeRater(), store.unscored(50), "prompt")  # type: ignore[arg-type]


def test_alerts_are_read_once_and_each_job_stored_once(
    new_database: NewDatabase, gmail: None
) -> None:
    new_database("mentor_test_jobs_ingest")
    assert desk.check_alerts() == 4  # 3 in the first alert, 1 new in the second
    assert desk.check_alerts() == 0  # both emails already read
    assert store.counts() == {"new": 4}


def test_daily_shortlist_then_status_updates_and_follow_up(
    new_database: NewDatabase, gmail: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_database("mentor_test_jobs_digest")
    desk.check_alerts()
    score_everything()
    monkeypatch.setattr(desk, "check_alerts", lambda: 0)
    monkeypatch.setattr(desk, "score_new", lambda: 0)

    morning = datetime(2026, 10, 20, 8, 15, tzinfo=HKT)
    digest = desk.tick(morning)
    assert digest is not None
    # Best fit first; the investment job (score 20) is below the charter's minimum of 50.
    water, professor = store.find("Water")[0], store.find("Professor")[0]
    assert digest.index(f"J{water.ref}") < digest.index(f"J{professor.ref}")
    assert "Investment Associate" not in digest
    assert desk.tick(morning + timedelta(hours=1)) is None  # once a day
    assert store.by_ref(water.ref).status == "shortlisted"  # type: ignore[union-attr]

    reply = update_job.invoke(
        {"job": f"J{water.ref}", "status": "Applied", "notes": "via Arup site"}
    )
    follow_up = datetime.now(HKT).date() + timedelta(days=7)  # the tool uses the real clock
    assert "is now applied" in reply
    assert f"Follow-up reminder set for {follow_up:%a %d %b}" in reply
    assert "is now saved" in update_job.invoke({"job": f"J{professor.ref}", "status": "saved"})
    assert "Not updated" in update_job.invoke({"job": f"J{professor.ref}", "status": "maybe"})
    assert "Not updated" in update_job.invoke({"job": "J99999", "status": "saved"})

    listing = find_jobs.invoke({"query": "water"})
    assert f"J{water.ref} Senior Water Engineer" in listing and "notes: via Arup site" in listing

    a_week_later = desk.tick(datetime.combine(follow_up, datetime.min.time(), HKT).replace(hour=8))
    assert a_week_later is not None and "**Follow-ups due**" in a_week_later


def test_drafts_need_the_gmail_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_permission(*args: object) -> str:
        raise GoogleNotConnectedError("Mentor doesn't have permission to create Gmail drafts yet.")

    monkeypatch.setattr("mentor.agent.tools.gmail.create_draft", no_permission)
    assert "permission" in draft_email.invoke({"to": "hr@arup.com", "subject": "x", "body": "y"})
    assert "isn't an email address" in draft_email.invoke(
        {"to": "Arup HR", "subject": "x", "body": "y"}
    )
