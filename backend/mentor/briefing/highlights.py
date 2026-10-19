"""Twice-weekly research highlights (OpenAlex), both from high-impact journals: the most-cited
recent papers on the owner's research topics, and new papers on those topics.

"High impact" means a journal in config/briefing.yaml's top_journals, chosen by OpenAlex's
two-year mean citedness (a free stand-in for the Journal Impact Factor, which is Clarivate's).
"""

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from mentor.briefing.sources import HEADERS, HighlightsConfig, clean, load_config
from mentor.charter import get_charter
from mentor.db.models import ResearchHighlight
from mentor.db.session import get_sessionmaker
from mentor.network.experts import any_phrase_query, short_id

log = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org/works"
FETCH = 25  # candidates per list, before dropping papers already sent
RETRY_AFTER = 2.0  # seconds


@dataclass(frozen=True)
class Highlight:
    work_id: str
    kind: str  # cited | top_journal
    title: str
    url: str
    journal: str | None
    published_on: date | None
    citations: int
    top_1_percent: bool = False


def parse(work: Mapping[str, Any], kind: str) -> Highlight | None:
    title = clean(work.get("title") or "", 300)
    if not title:
        return None
    source = (work.get("primary_location") or {}).get("source") or {}
    published = work.get("publication_date")
    percentile = work.get("citation_normalized_percentile") or {}
    return Highlight(
        work_id=short_id(work["id"]),
        kind=kind,
        title=title,
        url=work.get("doi") or work["id"],
        journal=source.get("display_name"),
        published_on=date.fromisoformat(published) if published else None,
        citations=int(work.get("cited_by_count") or 0),
        top_1_percent=bool(percentile.get("is_in_top_1_percent")),
    )


def fetch(
    topics: Mapping[str, Sequence[str]],
    since: date,
    kind: str,
    client: httpx.Client,
    issns: Sequence[str],
) -> list[Highlight]:
    phrases = [phrase for group in topics.values() for phrase in group]
    filters = [
        f"title_and_abstract.search:{any_phrase_query(phrases)}",
        f"primary_location.source.issn:{'|'.join(issns)}",
        f"from_publication_date:{since}",
        "type:article",
    ]
    params = {
        "filter": ",".join(filters),
        "sort": "cited_by_count:desc" if kind == "cited" else "publication_date:desc",
        "per-page": FETCH,
        "select": (
            "id,doi,title,publication_date,cited_by_count,primary_location,"
            "citation_normalized_percentile"
        ),
    }
    response = client.get(OPENALEX, params=params)
    if response.status_code == 429:  # OpenAlex asks for a short pause between bursts
        time.sleep(RETRY_AFTER)
        response = client.get(OPENALEX, params=params)
    response.raise_for_status()
    found = (parse(work, kind) for work in response.json().get("results", []))
    return [h for h in found if h is not None]


def already_sent(work_ids: Sequence[str]) -> set[str]:
    query = select(ResearchHighlight.work_id).where(ResearchHighlight.work_id.in_(work_ids))
    with get_sessionmaker()() as session:
        return set(session.scalars(query))


def save_sent(highlights: Sequence[Highlight], now: datetime) -> None:
    with get_sessionmaker()() as session:
        for h in highlights:
            session.execute(
                insert(ResearchHighlight)
                .values(
                    work_id=h.work_id,
                    kind=h.kind,
                    title=h.title,
                    url=h.url,
                    journal=h.journal,
                    published_on=h.published_on,
                    citations=h.citations,
                    sent_at=now,
                )
                .on_conflict_do_nothing(index_elements=["work_id"])
            )
        session.commit()


def fresh(highlights: Sequence[Highlight], limit: int, skip: set[str]) -> list[Highlight]:
    """Papers not sent before (and not already picked), up to `limit`."""
    sent = already_sent([h.work_id for h in highlights]) | skip
    return [h for h in highlights if h.work_id not in sent][:limit]


def format_highlight(h: Highlight) -> str:
    details = [h.journal or "OpenAlex"]
    if h.kind == "cited":
        if h.published_on:
            details.append(f"{h.published_on:%b %Y}")
        cited = f"{h.citations} citations"
        details.append(f"{cited}, top 1% for its field and year" if h.top_1_percent else cited)
    elif h.published_on:
        details.append(f"{h.published_on:%d %b}")
    return f"• {h.title} — {' · '.join(details)}\n  {h.url}"


def due(now: datetime, config: HighlightsConfig) -> bool:
    return now.strftime("%A").lower() in {day.lower() for day in config.days}


def section(now: datetime, client: httpx.Client | None = None) -> str | None:
    """The highlights for the morning message on the configured days; marks them as sent."""
    config = load_config().highlights
    topics = get_charter().research_topics
    issns = list(config.top_journals.values())
    if not topics or not issns or not due(now, config):
        return None
    own_client = client is None
    client = client or httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True)
    parts: list[str] = []
    try:
        cited_since = now.date() - timedelta(days=round(config.cited_months * 30.4))
        found = fetch(topics, cited_since, "cited", client, issns)
        cited = fresh(found, config.cited_count, set())
        if cited:
            parts.append(
                f"**Most cited in your field since {cited_since:%b %Y}**\n"
                + "\n".join(map(format_highlight, cited))
            )
        journal_since = now.date() - timedelta(days=config.journal_days)
        new = fetch(topics, journal_since, "top_journal", client, issns)
        top = fresh(new, config.journal_count, {h.work_id for h in cited})
        if top:
            parts.append(
                "**New in high-impact journals**\n" + "\n".join(map(format_highlight, top))
            )
        picked = cited + top
    finally:
        if own_client:
            client.close()
    if not parts:
        return None
    save_sent(picked, now)
    return "**Research highlights**\n\n" + "\n\n".join(parts)
