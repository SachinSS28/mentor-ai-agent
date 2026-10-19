"""Where briefing items come from: OpenAlex (papers) and RSS feeds (journals, news, funding).

Sources are listed in config/briefing.yaml. One failing source is logged and skipped, never fatal.
"""

import html
import logging
import re
from calendar import timegm
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml
from pydantic import BaseModel

from mentor.settings import get_settings

log = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org/works"
HEADERS = {"User-Agent": "Mentor/1.0 (personal research briefing)"}
PER_SOURCE = 15  # newest items taken from each feed or search
TAG = re.compile(r"<[^>]+>")
YEAR = re.compile(r"\b(20\d\d)\b")


class FilteredFeed(BaseModel):
    url: str
    keywords: list[str] = []


type Feed = str | FilteredFeed


class HighlightsConfig(BaseModel):
    """Twice-weekly research highlights from OpenAlex, on the charter's research_topics."""

    days: list[str] = ["Monday", "Thursday"]
    cited_months: int = 24  # the most-cited papers published in this many months
    cited_count: int = 4
    journal_days: int = 4  # new papers in top journals since this many days ago
    journal_count: int = 4
    top_journals: dict[str, str] = {}  # name: ISSN


class BriefingConfig(BaseModel):
    min_score: int = 60
    lookback_days: int = 3
    per_section: dict[str, int] = {"paper": 4, "journal": 3, "news": 3, "funding": 2}
    papers: dict[str, list[str]] = {}
    journals: dict[str, Feed] = {}
    news: dict[str, Feed] = {}
    funding: dict[str, Feed] = {}
    highlights: HighlightsConfig = HighlightsConfig()


def load_config(path: Path | None = None) -> BriefingConfig:
    path = path or get_settings().config_dir / "briefing.yaml"
    if not path.exists():
        return BriefingConfig()
    return BriefingConfig.model_validate(yaml.safe_load(path.read_text()) or {})


@dataclass(frozen=True)
class Item:
    kind: str  # paper | journal | news | funding
    source: str
    title: str
    url: str
    snippet: str | None = None
    published_at: datetime | None = None
    topic: str | None = None


def clean(text: str, limit: int = 400) -> str:
    """Plain text from feed HTML, whitespace collapsed, cut at a word boundary."""
    plain = " ".join(html.unescape(TAG.sub(" ", text)).split())
    return plain if len(plain) <= limit else plain[:limit].rsplit(" ", 1)[0] + "…"


def abstract_text(inverted: dict[str, list[int]] | None, limit: int = 400) -> str | None:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inverted:
        return None
    words = sorted(
        (position, word) for word, positions in inverted.items() for position in positions
    )
    return clean(" ".join(word for _, word in words), limit)


def openalex_papers(
    topic: str, phrase: str, since: datetime, until: datetime, client: httpx.Client
) -> list[Item]:
    params: dict[str, Any] = {
        "filter": (
            f"title_and_abstract.search:{phrase},from_publication_date:{since.date()},"
            f"to_publication_date:{until.date()},type:article,primary_location.source.type:journal"
        ),
        "sort": "publication_date:desc",
        "per-page": PER_SOURCE,
        "select": "id,doi,title,publication_date,primary_location,abstract_inverted_index",
    }
    response = client.get(OPENALEX, params=params)
    response.raise_for_status()
    items = []
    for work in response.json().get("results", []):
        title = clean(work.get("title") or "", 300)
        if not title:
            continue
        source = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
        published = work.get("publication_date")
        items.append(
            Item(
                kind="paper",
                source=source or "OpenAlex",
                title=title,
                url=work.get("doi") or work["id"],
                snippet=abstract_text(work.get("abstract_inverted_index")),
                published_at=datetime.fromisoformat(published).replace(tzinfo=UTC)
                if published
                else None,
                topic=topic,
            )
        )
    return items


def is_past_event(title: str, year: int) -> bool:
    """A call or conference whose title names only past years ("ONICWater 2020")."""
    years = [int(y) for y in YEAR.findall(title)]
    return bool(years) and max(years) < year


def entry_time(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(timegm(parsed), UTC) if parsed else None


def feed_items(
    kind: str, name: str, feed: Feed, since: datetime, client: httpx.Client
) -> list[Item]:
    url, keywords = (feed, []) if isinstance(feed, str) else (feed.url, feed.keywords)
    response = client.get(url)
    response.raise_for_status()
    items = []
    for entry in feedparser.parse(response.content).entries:
        title, link = clean(entry.get("title", ""), 300), entry.get("link")
        if not title or not link:
            continue
        published = entry_time(entry)
        if published is not None and published < since:
            continue
        # Some event feeds (WikiCFP) carry no dates, so old calls would otherwise come back.
        if kind == "funding" and is_past_event(title, since.year):
            continue
        snippet = clean(entry.get("summary", "")) or None
        text = f"{title} {snippet or ''}".lower()
        if keywords and not any(word.lower() in text for word in keywords):
            continue
        items.append(Item(kind, name, title, link, snippet, published, topic=name))
        if len(items) >= PER_SOURCE:
            break
    return items


def collect_all(config: BriefingConfig, now: datetime) -> list[Item]:
    since = now - timedelta(days=config.lookback_days)
    items: list[Item] = []
    with httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS) as client:
        for topic, phrases in config.papers.items():
            for phrase in phrases:
                try:
                    items += openalex_papers(topic, phrase, since, now, client)
                except Exception as error:  # noqa: BLE001 - one bad source mustn't stop the rest
                    log.warning("Briefing: OpenAlex search %r failed: %s", phrase, error)
        for kind, feeds in (
            ("journal", config.journals),
            ("news", config.news),
            ("funding", config.funding),
        ):
            for name, feed in feeds.items():
                try:
                    items += feed_items(kind, name, feed, since, client)
                except Exception as error:  # noqa: BLE001
                    log.warning("Briefing: feed %r failed: %s", name, error)
    return items
