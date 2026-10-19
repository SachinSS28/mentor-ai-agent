from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from tests.helpers_jobs import ALERT, ALERT_HEADED, ALERT_HTMLISH

from mentor.db.models import Job
from mentor.jobs.alerts import parse_linkedin_alert
from mentor.jobs.desk import digest_due, format_digest, parse_scores
from mentor.jobs.store import parse_ref

HKT = ZoneInfo("Asia/Hong_Kong")


def test_every_job_block_in_an_alert_is_parsed() -> None:
    jobs = parse_linkedin_alert(ALERT)
    assert [(j.external_id, j.title, j.company, j.location) for j in jobs] == [
        ("4100000001", "Senior Water Engineer", "Arup", "Hong Kong SAR"),
        (
            "4100000002",
            "Research Assistant Professor",
            "The Hong Kong Polytechnic University",
            "Hung Hom, Kowloon, Hong Kong SAR",
        ),
        ("4100000003", "Hydraulic Modeller", "Jacobs", None),
    ]
    assert jobs[1].insights == ("1 school alum", "Easy Apply")
    assert jobs[0].alert == "water engineer in Hong Kong"
    # Tracking parameters are dropped from the link.
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4100000001/"


def test_headings_links_and_html_around_jobs_are_skipped() -> None:
    [headed] = parse_linkedin_alert(ALERT_HEADED)
    assert (headed.title, headed.company, headed.location) == (
        "Technical Officer",
        "Jacobs",
        "Hong Kong",
    )
    assert headed.insights == ("3 connections",)
    [htmlish] = parse_linkedin_alert(ALERT_HTMLISH)
    assert (htmlish.title, htmlish.company) == ("Senior Hydraulic Engineer", "AECOM")
    assert htmlish.insights == ("This company is actively hiring", "Apply with resume & profile")
    assert htmlish.alert == "water engineer jobs in Hong Kong"


def test_text_without_job_blocks_gives_no_jobs() -> None:
    assert parse_linkedin_alert("Your job alert for x\nNothing here today.") == []


@pytest.mark.parametrize(
    ("hour", "sent_on", "due"),
    [
        (7, None, False),  # before 08:00
        (8, None, True),
        (11, "2026-10-20", False),  # already sent today
        (11, "2026-10-19", True),  # sent yesterday: the Mac slept through 08:00 today
    ],
)
def test_digest_goes_out_once_a_day_at_or_after_its_time(
    hour: int, sent_on: str | None, due: bool
) -> None:
    now = datetime(2026, 10, 20, hour, 5, tzinfo=HKT)
    assert digest_due(now, sent_on, "08:00") is due


def job(ref: int, title: str, score: int, **extra: object) -> Job:
    return Job(
        ref=ref,
        title=title,
        company="Arup",
        location="Hong Kong",
        url=f"https://www.linkedin.com/jobs/view/{ref}/",
        fit_score=score,
        fit_reason="Matches water management focus",
        **extra,
    )


def test_digest_lists_jobs_and_follow_ups() -> None:
    applied = job(3, "Hydraulic Modeller", 70, applied_at=datetime(2026, 10, 13, tzinfo=UTC))
    text = format_digest([job(12, "Senior Water Engineer", 92)], [applied], date(2026, 10, 20))
    assert text is not None
    assert text.startswith("**Job shortlist, Tue 20 Oct**")
    assert "**J12** Senior Water Engineer — Arup, Hong Kong · fit 92%" in text
    assert "https://www.linkedin.com/jobs/view/12/" in text
    assert "**Follow-ups due**" in text and "(applied 13 Oct)" in text


def test_nothing_to_report_sends_nothing() -> None:
    assert format_digest([], [], date(2026, 10, 20)) is None


@pytest.mark.parametrize("text", ["J12", "j12", "#12", " 12 "])
def test_job_numbers_are_read_however_they_are_written(text: str) -> None:
    assert parse_ref(text) == 12


def test_a_word_is_not_a_job_number() -> None:
    with pytest.raises(ValueError, match="job number"):
        parse_ref("the second one")


def test_score_lines_are_read_even_with_formatting_around_them() -> None:
    reply = (
        "Scores:\n* **J12** | 85 | Water role, matches PhD\nJ13 | 40% | Finance, wrong field\nnoise"
    )
    assert parse_scores(reply) == [
        (12, 85, "Water role, matches PhD"),
        (13, 40, "Finance, wrong field"),
    ]
