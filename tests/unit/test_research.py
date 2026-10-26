"""Scholarly reading, research reports, the library's passages and the activity journal, without
a database, the network or a model."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from mentor import activity
from mentor.charter import ActivityConfig
from mentor.db.models import ActivityEntry
from mentor.research import library, reports, scholar

HKT = ZoneInfo("Asia/Hong_Kong")

WORK = {
    "id": "https://openalex.org/W42",
    "doi": "https://doi.org/10.1016/j.watres.2021.117961",
    "title": "Dynamics of pathogens during riverbank filtration",
    "publication_year": 2021,
    "cited_by_count": 24,
    "fwci": 0.84,
    "primary_location": {"source": {"display_name": "Water Research"}},
    "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
    "authorships": [{"author": {"display_name": "A. Author"}}],
    "abstract_inverted_index": {"Pathogens": [0], "decline": [1]},
    "related_works": ["https://openalex.org/W7"],
    "referenced_works": ["https://openalex.org/W8", "https://openalex.org/W9"],
}


def test_papers_carry_impact_and_open_access() -> None:
    paper = scholar.parse(WORK)
    assert (paper.id, paper.authors, paper.abstract) == ("W42", ["A. Author"], "Pathogens decline")
    assert (paper.related, paper.references) == (["W7"], ["W8", "W9"])
    assert scholar.line(paper) == (
        "- W42 · Dynamics of pathogens during riverbank filtration (2021 · Water Research · "
        "24 citations · FWCI 0.8 · open access) https://doi.org/10.1016/j.watres.2021.117961"
    )


def openalex(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_papers_are_found_by_doi_id_or_title() -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path + ("?search" if "search" in request.url.params else ""))
        if "search" in request.url.params:
            return httpx.Response(200, json={"results": [WORK]})
        return httpx.Response(200, json=WORK)

    client = openalex(handle)
    assert scholar.find("see doi 10.1016/j.watres.2021.117961.", client) is not None
    assert scholar.find("w42", client) is not None
    assert scholar.find("riverbank pathogens", client) is not None
    assert asked == [
        "/works/doi:10.1016/j.watres.2021.117961",
        "/works/W42",
        "/works?search",
    ]


def test_a_landing_page_is_not_taken_for_the_pdf() -> None:
    client = openalex(lambda request: httpx.Response(200, text="<html>Sign in</html>"))
    assert scholar.full_text(scholar.parse(WORK), client) is None


def test_semantic_scholar_being_busy_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scholar.httpx, "get", lambda *a, **k: httpx.Response(429))
    assert scholar.tldr(scholar.parse(WORK)) is None


def test_long_papers_are_cut_to_the_relevant_parts() -> None:
    filler = "\n\n".join(
        f"Paragraph {n} about sediment transport and turbidity." for n in range(400)
    )
    text = f"Abstract on riverbank filtration.\n\n{filler}\n\nVirus removal reached 4 log units."
    focused = scholar.focus(text, "virus removal log", limit=300)
    assert "Virus removal reached 4 log units." in focused and len(focused) <= 400
    start_and_end = scholar.focus(text, "", limit=300)
    assert start_and_end.startswith("Abstract") and start_and_end.endswith("4 log units.")


def test_research_plans_web_and_paper_searches(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = (
        "1. web: Hong Kong water leakage rate 2026\n- paper: acoustic leak detection review\nweb:"
    )
    monkeypatch.setattr(reports, "ask_model", lambda role, prompt, system=None: plan)
    assert reports.plan("How does Hong Kong cut water leakage?") == (
        ["Hong Kong water leakage rate 2026"],
        ["acoustic leak detection review"],
    )
    monkeypatch.setattr(reports, "ask_model", lambda role, prompt, system=None: "no idea")
    assert reports.plan("Leakage?") == (["Leakage?"], ["Leakage?"])


def test_reports_end_with_the_real_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "## Summary\n- Leakage fell to 15% [1]\n## Findings\nText [2]\n## Sources\n1. made up"
    monkeypatch.setattr(reports, "ask_model", lambda role, prompt, system=None: body)
    monkeypatch.setattr(reports, "mentor_system_prompt", lambda: "You are Mentor.")
    sources = [
        reports.Source(1, "WSD annual report", "https://wsd.gov.hk/r", "web", "", "- 15%"),
        reports.Source(2, "Leak review (Water Research, 2025)", "https://doi.org/x", "paper", ""),
    ]
    text = reports.write("How does Hong Kong cut water leakage?", sources)
    assert text.startswith("# How does Hong Kong cut water leakage?\n\n## Summary")
    assert "made up" not in text
    assert text.endswith(
        "## Sources\n1. [WSD annual report](https://wsd.gov.hk/r)\n"
        "2. [Leak review (Water Research, 2025)](https://doi.org/x)\n"
    )
    assert reports.summary_of(text) == "- Leakage fell to 15% [1]"


def test_a_report_runs_from_plan_to_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from mentor.mcp_servers.web_tools import search as web

    answers = {
        "Plan": "web: leakage Hong Kong\npaper: leak detection",
        "Source [1]": "- WSD cut leakage to 15% by 2025",
        "Source [2]": "NONE",
        "Write": "## Summary\n- Leakage fell to 15% [1]",
    }

    def model(role: str, prompt: str, system: str | None = None) -> str:
        return next(
            text for key, text in answers.items() if prompt.startswith(key) or key in prompt
        )

    monkeypatch.setattr(reports, "ask_model", model)
    monkeypatch.setattr(reports, "mentor_system_prompt", lambda: "You are Mentor.")
    page = web.WebResult("WSD leakage", "https://wsd.gov.hk/leak", "snippet")
    monkeypatch.setattr(reports.web, "search", lambda q, max_results: [page])
    monkeypatch.setattr(reports.web, "read_many", lambda urls, q: {page.url: "Leakage 15%"})
    monkeypatch.setattr(
        reports.scholar, "search", lambda q, since_year, limit: [scholar.parse(WORK)]
    )
    finished: dict[str, Any] = {}
    monkeypatch.setattr(reports, "finish", lambda ref, **values: finished.update(values))

    job = reports.ResearchReport(ref=3, question="How does Hong Kong cut water leakage?")
    summary, text = reports.run(job)
    assert summary == "- Leakage fell to 15% [1]"
    assert "1. [WSD leakage](https://wsd.gov.hk/leak)" in text
    assert "riverbank" not in text  # the paper had nothing relevant, so it isn't listed
    assert finished["status"] == "done" and finished["sources"][0]["url"] == page.url
    assert reports.file_name(job) == "R3-how-does-hong-kong-cut-water-leakage.md"


def test_library_passages_overlap_and_stay_small() -> None:
    sentences = " ".join(f"Sentence number {n} about riverbank filtration." for n in range(200))
    passages = library.chunks(sentences, size=300, overlap=60)
    assert all(len(p) <= 300 for p in passages)
    assert len(passages) > 10
    assert passages[1].split(" ")[0] in passages[0]  # each starts inside the previous one
    assert library.chunks("") == []


CONFIG = ActivityConfig(
    private_apps=["WhatsApp"], private_sites=["hsbc", "mail.google.com"], hide_titles=["Mail"]
)
NOW = datetime(2026, 10, 26, 14, 0, tzinfo=HKT)


def test_activity_privacy_rules() -> None:
    def seen(app: str, title: str | None = None, url: str | None = None, **kw: Any) -> Any:
        return activity.entry(activity.Seen(app, title, url, **kw), CONFIG, NOW)

    udemy = seen("Safari", "Course: IELTS Band 7+ | Udemy", "https://www.udemy.com/course/ielts")
    assert (udemy.app, udemy.title, udemy.domain) == (
        "Safari",
        "Course: IELTS Band 7+ | Udemy",
        "udemy.com",
    )
    for private in (
        seen("WhatsApp", "Family"),
        seen("Safari", "Accounts", "https://www.hsbc.com.hk/login"),
        seen("Google Chrome", "Inbox", "https://mail.google.com/mail/u/0"),
        seen("Google Chrome", "Anything", "https://example.org", incognito=True),
    ):
        assert (private.app, private.title, private.domain) == ("(private)", None, None)
    assert seen("Mail", "Re: salary offer").title is None


def test_the_front_window_and_browser_tab_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {
        "System Events": "Google Chrome\tIELTS listening",
        "Google Chrome": "Section 6 — Udemy\thttps://www.udemy.com/x\tincognito",
    }
    monkeypatch.setattr(
        activity, "osascript", lambda script: next(v for k, v in answers.items() if k in script)
    )
    assert activity.front() == activity.Seen(
        "Google Chrome", "Section 6 — Udemy", "https://www.udemy.com/x", True
    )
    for script in (activity.SAFARI_TAB, activity.CHROMIUM_TAB):  # inside, `tab` is a browser tab
        assert script.rindex("end tell") < script.index("& tab &")
    answers["System Events"] = "Code\tmentor — tools.py"
    assert activity.front() == activity.Seen("Code", "mentor — tools.py")


def test_activity_totals_and_periods() -> None:
    items = [ActivityEntry(at=NOW, app="Safari", title="IELTS | Udemy") for _ in range(45)]
    items += [ActivityEntry(at=NOW, app="(private)") for _ in range(10)]
    counted = activity.totals(items)
    assert counted.most_common(2) == [("Safari — IELTS | Udemy", 45), ("Private", 10)]
    assert (
        activity.label(ActivityEntry(at=NOW, app="Code", title="tools.py")) == "VS Code — tools.py"
    )
    assert activity.duration(125) == "2 h 5 min" and activity.duration(45) == "45 min"
    start, end, name = activity.period("last 2 hours", NOW)
    assert (end - start, name) == (timedelta(hours=2), "the last 2 hours")
    start, _, name = activity.period("yesterday", NOW)
    assert (start.day, name) == (25, "yesterday")
