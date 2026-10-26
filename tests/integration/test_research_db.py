"""The library, research report queue and activity journal on a throwaway database."""

import hashlib
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from tests.integration.conftest import NewDatabase

from mentor import activity
from mentor.agent.tools import my_activity, pause_watching, search_my_documents, start_research
from mentor.db.models import LIBRARY_DIMENSIONS, ActivityEntry
from mentor.research import library, reports

HKT = ZoneInfo("Asia/Hong_Kong")


class WordEmbeddings:
    """Stands in for EmbeddingGemma: texts sharing words get similar vectors."""

    def vector(self, text: str) -> list[float]:
        counts = [0.0] * LIBRARY_DIMENSIONS
        body = text.split("|", 1)[-1].lower()  # drop the "task:"/"title:" prompt
        for word in body.replace(":", " ").split():
            counts[int(hashlib.sha256(word.encode()).hexdigest(), 16) % LIBRARY_DIMENSIONS] += 1
        norm = math.sqrt(sum(c * c for c in counts)) or 1.0
        return [c / norm for c in counts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.vector(text)


@pytest.fixture
def embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "get_embeddings", WordEmbeddings)


def test_the_library_finds_passages_by_meaning(new_database: NewDatabase, embeddings: None) -> None:
    new_database("mentor_test_library")
    assert "library is empty" in search_my_documents.invoke({"query": "anything"})
    library.replace(
        "drive", "f1", "CV 2026.pdf", "v1", "Riverbank filtration pilot in Shatin. " * 3
    )
    library.replace("drive", "f2", "Teaching statement", "v1", "I teach hydraulics with projects.")
    library.index_report(4, "Leakage in Hong Kong", "Hong Kong leakage fell to 15 percent.")
    assert library.size() == (3, 3)
    assert library.versions("drive") == {"f1": "v1", "f2": "v1"}

    found = library.search("riverbank filtration Shatin", limit=2)
    assert found[0].title == "CV 2026.pdf" and found[0].distance < found[1].distance
    reply = search_my_documents.invoke({"query": "hydraulics teaching"})
    assert reply.startswith('<untrusted source="library">\n[Teaching statement]')

    library.replace("drive", "f1", "CV 2026.pdf", "v2", "Updated CV.")  # re-indexed, not doubled
    library.forget("drive", ["f1"])  # f2 was deleted from Drive
    assert library.size() == (2, 2)
    assert library.versions("drive") == {"f1": "v2"}


def test_research_reports_queue_one_at_a_time(new_database: NewDatabase) -> None:
    new_database("mentor_test_reports")
    reply = start_research.invoke({"question": "How does Hong Kong reduce water leakage?"})
    assert reply.startswith("Research R1 started on: How does Hong Kong reduce water leakage?")
    assert start_research.invoke({"question": "leakage"}).startswith("Not started")
    reports.queue("Second question about riverbank filtration")

    first = reports.claim()
    assert first is not None and first.ref == 1 and first.status == "running"
    second = reports.claim()
    assert second is not None and second.ref == 2
    assert reports.claim() is None
    reports.finish(2, status="done", summary="- ok", report="# r")
    reports.requeue_stale()  # R1 was cut short by a restart
    again = reports.claim()
    assert again is not None and again.ref == 1


def test_activity_journal_summaries_and_pause(new_database: NewDatabase) -> None:
    new_database("mentor_test_activity")
    now = datetime.now(HKT)
    for minute in range(30):
        activity.record(
            ActivityEntry(at=now - timedelta(minutes=minute + 1), app="Safari", title="IELTS")
        )
    for minute in range(5):
        activity.record(ActivityEntry(at=now - timedelta(minutes=40 + minute), app="(private)"))
    activity.record(ActivityEntry(at=now - timedelta(days=9), app="Old", title="gone"))

    reply = my_activity.invoke({"period": "last 2 hours"})
    assert reply == (
        "Activity for the last 2 hours:\nActive at the Mac: 35 min\n"
        "- Safari — IELTS: 30 min\n- Private: 5 min"
    )
    activity.prune(now, keep_days=7)
    assert all(e.app != "Old" for e in activity.entries(now - timedelta(days=30), now))

    assert pause_watching.invoke({"hours": 2}).startswith("The activity journal is paused until")
    assert activity.paused(now + timedelta(hours=1))
    assert "paused right now" in my_activity.invoke({})
    assert pause_watching.invoke({"hours": 0}) == "The activity journal is recording again."
    assert not activity.paused(datetime.now(HKT) + timedelta(seconds=1))
