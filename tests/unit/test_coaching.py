"""Tasks and reminders, calendar blocks, Drive documents and research highlights, without a
database or Google."""

import io
import zipfile
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from mentor.briefing import highlights
from mentor.briefing.sources import HighlightsConfig
from mentor.channels.telegram.bot import reminder_text
from mentor.db.models import Task
from mentor.mcp_servers.google_tools import calendar, drive
from mentor.mcp_servers.mentor_core.tasks import parse_ref, parse_when
from mentor.network.experts import any_phrase_query

HKT = ZoneInfo("Asia/Hong_Kong")


def test_times_are_read_in_the_owners_time_zone() -> None:
    assert parse_when("2026-10-23 15:00", HKT) == datetime(2026, 10, 23, 15, 0, tzinfo=HKT)
    assert parse_when("2026-10-23T15:00", HKT) == datetime(2026, 10, 23, 15, 0, tzinfo=HKT)
    assert parse_when("2026-10-23", HKT) == datetime(2026, 10, 23, 23, 59, tzinfo=HKT)
    assert parse_when("2026-10-23", HKT, time(9, 0)) == datetime(2026, 10, 23, 9, 0, tzinfo=HKT)
    assert parse_when("  ", HKT) is None
    with pytest.raises(ValueError, match="2026-10-23 15:00"):
        parse_when("next Friday", HKT)


def test_task_numbers_are_read_flexibly() -> None:
    assert parse_ref("T3") == parse_ref("t3") == parse_ref("#3") == parse_ref("3") == 3
    assert parse_ref("send the report") is None


def test_reminder_names_the_task_and_how_to_close_it() -> None:
    task = Task(ref=7, title="Email Prof. Chan", notes="about the RBF dataset")
    task.due = datetime(2026, 10, 23, 18, 0, tzinfo=HKT)
    assert reminder_text(task) == (
        "⏰ **Reminder, T7:** Email Prof. Chan (due Fri 23 Oct 18:00)\n"
        'about the RBF dataset\nSay "done T7" when it\'s done.'
    )


def test_calendar_blocks_never_invite_anyone() -> None:
    start = datetime(2026, 10, 23, 7, 0, tzinfo=HKT)
    body = calendar.block_body(" Meditation ", start, start.replace(hour=7, minute=30), "")
    assert "attendees" not in body
    assert body == {
        "summary": "Meditation",
        "description": calendar.ADDED_BY,
        "start": {"dateTime": "2026-10-23T07:00:00+08:00", "timeZone": "Asia/Hong_Kong"},
        "end": {"dateTime": "2026-10-23T07:30:00+08:00", "timeZone": "Asia/Hong_Kong"},
    }


def test_overlapping_events_are_spotted() -> None:
    def event(start: int, end: int) -> calendar.Event:
        day = datetime(2026, 10, 23, tzinfo=HKT)
        return calendar.Event("x", day.replace(hour=start), day.replace(hour=end), None, 0)

    start, end = datetime(2026, 10, 23, 9, tzinfo=HKT), datetime(2026, 10, 23, 10, tzinfo=HKT)
    allday = calendar.Event("Holiday", date(2026, 10, 23), date(2026, 10, 24), None, 0)
    found = calendar.overlapping([event(8, 9), event(9, 11), event(10, 12), allday], start, end)
    assert [(e.start.hour, e.end.hour) for e in found] == [(9, 11)]  # type: ignore[union-attr]


def word_file(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")
    return buffer.getvalue()


def test_word_documents_are_read_as_paragraphs() -> None:
    text = drive.docx_text(word_file("Sachin &amp; team", "Water Research, 2025"))
    assert drive.tidy(text) == "Sachin & team\nWater Research, 2025"


def test_documents_are_found_by_name_or_words() -> None:
    files = [
        drive.DriveFile("1", "CV 2026.pdf", drive.PDF, "2026-10-01"),
        drive.DriveFile("2", "CV academic.docx", drive.DOCX, "2026-09-01"),
        drive.DriveFile("3", "Publications.txt", "text/plain", "2026-08-01"),
    ]
    assert drive.pick(files, "cv academic.docx").id == "2"
    assert drive.pick(files, "cv").id == "1"  # newest first
    assert drive.pick(files, "publications").id == "3"
    with pytest.raises(LookupError, match="In the folder: CV 2026.pdf"):
        drive.pick(files, "teaching statement")
    assert drive.quoted("Sachin's CV") == "'Sachin\\'s CV'"


def test_long_documents_are_cut_at_a_word() -> None:
    text = drive.tidy("word " * 5000, limit=100)
    assert len(text) <= 102 and text.endswith(" …")


def test_topics_become_one_search() -> None:
    assert any_phrase_query(["riverbank filtration", "leak detection + water"]) == (
        '("riverbank filtration") OR ("leak detection" AND "water")'
    )


WORK = {
    "id": "https://openalex.org/W9",
    "doi": "https://doi.org/10.1/w9",
    "title": "Machine learning for <i>drinking water</i> quality",
    "publication_date": "2025-03-02",
    "cited_by_count": 81,
    "primary_location": {"source": {"display_name": "Water Research"}},
    "citation_normalized_percentile": {"is_in_top_1_percent": True},
}


def test_highlights_show_citations_or_publication_day() -> None:
    cited = highlights.parse(WORK, "cited")
    assert cited is not None
    assert highlights.format_highlight(cited) == (
        "• Machine learning for drinking water quality — Water Research · Mar 2025 · "
        "81 citations, top 1% for its field and year\n  https://doi.org/10.1/w9"
    )
    new = highlights.parse(WORK, "top_journal")
    assert new is not None
    assert highlights.format_highlight(new) == (
        "• Machine learning for drinking water quality — Water Research · 02 Mar\n"
        "  https://doi.org/10.1/w9"
    )


def test_highlights_come_on_their_days_only() -> None:
    config = HighlightsConfig(days=["Monday", "Thursday"])
    assert highlights.due(datetime(2026, 10, 22, 8, tzinfo=HKT), config)  # Thursday
    assert not highlights.due(datetime(2026, 10, 21, 8, tzinfo=HKT), config)


def test_both_lists_come_from_high_impact_journals(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        return httpx.Response(200, json={"results": [WORK]})

    monkeypatch.setattr(highlights, "already_sent", lambda ids: set())
    saved: list[Any] = []
    monkeypatch.setattr(highlights, "save_sent", lambda items, now: saved.extend(items))
    client = httpx.Client(transport=httpx.MockTransport(handle))
    monday = datetime(2026, 10, 26, 8, 0, tzinfo=HKT)
    message = highlights.section(monday, client)

    assert message is not None and message.startswith("**Research highlights**")
    assert "**Most cited in your field since Oct 2024**" in message
    # The same paper isn't repeated in the second list.
    assert "New in high-impact journals" not in message
    assert [s.work_id for s in saved] == ["W9"]
    cited, new = requests
    assert cited["sort"] == "cited_by_count:desc" and new["sort"] == "publication_date:desc"
    for params in requests:
        assert "primary_location.source.issn:2398-9629|" in params["filter"]
        assert '("riverbank filtration")' in params["filter"]
    assert highlights.section(datetime(2026, 10, 27, 8, 0, tzinfo=HKT), client) is None


def test_calendar_tool_mentions_clashes(monkeypatch: pytest.MonkeyPatch) -> None:
    from mentor.agent.tools import add_calendar_block

    meeting = calendar.Event(
        "Lab meeting",
        datetime(2026, 10, 23, 7, 15, tzinfo=HKT),
        datetime(2026, 10, 23, 8, 0, tzinfo=HKT),
        None,
        3,
    )
    added: list[tuple[str, datetime, datetime]] = []
    monkeypatch.setattr(calendar, "list_events", lambda day, days, tz: [meeting])
    monkeypatch.setattr(
        calendar,
        "add_block",
        lambda title, start, end, notes: added.append((title, start, end)) or "https://cal/e1",
    )
    reply = add_calendar_block.invoke(
        {"title": "Meditation", "start": "2026-10-23 07:00", "minutes": 30}
    )
    assert reply == (
        "Added to your calendar: Meditation, Fri 23 Oct 07:00–07:30. It overlaps "
        "Fri 23 Oct 07:15–08:00: Lab meeting — 3 attendees. https://cal/e1"
    )
    assert added[0][2] - added[0][1] == timedelta(minutes=30)
    assert add_calendar_block.invoke({"title": "x", "start": "soon"}).startswith("Not added")


def test_documents_reach_the_model_marked_as_the_owners(monkeypatch: pytest.MonkeyPatch) -> None:
    from mentor.agent.tools import read_document

    found = drive.DriveFile("1", "CV 2026.pdf", drive.PDF, "2026-10-01")
    monkeypatch.setattr(drive, "read", lambda folder, name: (found, "Water engineer, PhD"))
    reply = read_document.invoke({"name": "CV"})
    assert reply.startswith('<untrusted source="drive" name="CV 2026.pdf">\nWater engineer, PhD')

    def missing(folder: str, name: str) -> Any:
        raise LookupError("There's no Google Drive folder named 'Mentor' yet.")

    monkeypatch.setattr(drive, "read", missing)
    assert read_document.invoke({"name": "CV"}).startswith("There's no Google Drive folder")
