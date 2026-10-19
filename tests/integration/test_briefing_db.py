"""The briefing and the combined 08:00 message on a throwaway database."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage
from tests.integration.conftest import NewDatabase

from mentor import morning
from mentor.agent.tools import find_research
from mentor.briefing import digest as briefing
from mentor.briefing import store
from mentor.briefing.sources import Item
from mentor.jobs import desk

HKT = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 20, 8, 30, tzinfo=HKT)


class KeywordRater:
    """Stands in for the model: relevant if the line mentions water or wastewater."""

    def invoke(self, messages: list[Any]) -> AIMessage:
        lines = []
        for n, line in re.findall(r"^(\d+) \|(.*)$", messages[-1].content, re.M):
            score = 88 if re.search(r"water", line, re.I) else 15
            lines.append(f"{n} | {score} | {'on topic' if score > 50 else 'off topic'}")
        return AIMessage("\n".join(lines))


def items() -> list[Item]:
    published = datetime(2026, 10, 19, tzinfo=UTC)
    return [
        Item(
            "paper",
            "Water Research",
            "N2O in wastewater plants",
            "https://doi.org/1",
            None,
            published,
        ),
        Item("paper", "Some Journal", "Protein folding", "https://doi.org/2", None, published),
        Item(
            "news", "Google News", "Water utility goes digital", "https://news/3", None, published
        ),
        Item("funding", "WikiCFP", "Water Week 2027 call", "https://cfp/4", None, published),
    ]


def test_items_are_stored_once_scored_and_sent_once(new_database: NewDatabase) -> None:
    new_database("mentor_test_briefing")
    assert store.save(items()) == 4
    assert store.save(items()) == 0  # same links
    briefing.score_items(KeywordRater(), store.unscored(50), "prompt")  # type: ignore[arg-type]

    text = briefing.section(NOW)
    assert text is not None and text.startswith("**Research & news**")
    assert "N2O in wastewater plants" in text and "Protein folding" not in text
    assert "**Industry & policy news**" in text and "**Funding calls & conferences**" in text
    assert briefing.section(NOW) is None  # everything good was sent

    found = find_research.invoke({"query": "wastewater", "days": 30})
    assert "N2O in wastewater plants" in found and "relevance 88%" in found


def test_morning_message_has_jobs_and_research_once_a_day(
    new_database: NewDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_database("mentor_test_morning")
    store.save(items())
    briefing.score_items(KeywordRater(), store.unscored(50), "prompt")  # type: ignore[arg-type]
    for name in ("check_alerts", "score_new"):
        monkeypatch.setattr(desk, name, lambda *a, **k: 0)
    for name in ("collect", "score_new"):
        monkeypatch.setattr(briefing, name, lambda *a, **k: 0)

    assert morning.tick(NOW.replace(hour=7)) is None  # before 08:00
    message = morning.tick(NOW)
    assert message is not None and "**Research & news**" in message
    assert morning.tick(NOW + timedelta(hours=2)) is None


def test_a_briefing_failure_never_blocks_the_jobs(
    new_database: NewDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_database("mentor_test_morning_failure")
    for name in ("check_alerts", "score_new"):
        monkeypatch.setattr(desk, name, lambda *a, **k: 0)
    monkeypatch.setattr(desk, "shortlist", lambda now: "**Job shortlist**")

    def broken(*args: object) -> None:
        raise RuntimeError("feed down")

    monkeypatch.setattr(briefing, "collect", broken)
    monkeypatch.setattr(briefing, "section", broken)
    assert morning.tick(NOW) == "**Job shortlist**"
