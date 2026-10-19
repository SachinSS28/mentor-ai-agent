"""People to network with: researchers and engineers at Hong Kong institutions who publish on the
owner's topics, from OpenAlex (free, public scholarly records).

Nothing here guesses anyone's nationality or background from their name: "career outside Hong
Kong" comes only from the institutions on a person's publication record, which can be incomplete.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

log = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org"
HEADERS = {"User-Agent": "Mentor/1.0 (personal networking assistant)"}
COUNTRY = "HK"
PER_QUERY = 50  # newest matching papers per phrase
ENRICH = 40  # candidates whose full career record is looked up
MIN_YEARS_ABROAD = 2  # a single co-affiliation on one paper doesn't make a career


@dataclass(frozen=True)
class Paper:
    title: str
    year: int | None
    url: str


@dataclass
class Candidate:
    author_id: str  # OpenAlex id, e.g. "A5080970906"
    name: str
    organization: str  # the Hong Kong institution on their latest matching paper
    topics: set[str] = field(default_factory=set)
    papers: list[Paper] = field(default_factory=list)
    orcid: str | None = None
    h_index: int | None = None
    abroad: list[str] = field(default_factory=list)  # "University of Sheffield (GB)"

    @property
    def profile_url(self) -> str:
        return self.orcid or f"https://openalex.org/{self.author_id}"


def phrase_query(phrase: str) -> str:
    """'leak detection + water' -> '"leak detection" "water"': each part matched exactly.
    Commas would split the OpenAlex filter, so they are dropped."""
    parts = [part.replace(",", " ").replace('"', "").strip() for part in phrase.split("+")]
    return " ".join(f'"{part}"' for part in parts if part)


def search_works(
    phrase: str, since: date, client: httpx.Client, exact: bool = True
) -> list[dict[str, Any]]:
    """Recent journal papers matching the phrase with at least one author in Hong Kong."""
    query = phrase_query(phrase) if exact else phrase.replace(",", " ")
    params: dict[str, Any] = {
        "filter": (
            f"title_and_abstract.search:{query},"
            f"authorships.institutions.country_code:{COUNTRY},"
            f"from_publication_date:{since},type:article"
        ),
        "sort": "publication_date:desc",
        "per-page": PER_QUERY,
        "select": "id,doi,title,publication_year,authorships",
    }
    response = client.get(f"{OPENALEX}/works", params=params)
    response.raise_for_status()
    results: list[dict[str, Any]] = response.json().get("results", [])
    return results


def short_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def add_works(found: dict[str, Candidate], works: Iterable[Mapping[str, Any]], topic: str) -> None:
    """Adds each paper to every author who signed it from a Hong Kong institution."""
    for work in works:
        title = " ".join((work.get("title") or "").split())
        if not title:
            continue
        paper = Paper(title, work.get("publication_year"), work.get("doi") or work["id"])
        for authorship in work.get("authorships") or []:
            author = authorship.get("author") or {}
            local = [
                i for i in authorship.get("institutions") or [] if i.get("country_code") == COUNTRY
            ]
            if not author.get("id") or not author.get("display_name") or not local:
                continue
            author_id = short_id(author["id"])
            person = found.setdefault(
                author_id,
                Candidate(author_id, author["display_name"], local[0]["display_name"]),
            )
            person.topics.add(topic)
            if paper.url not in {p.url for p in person.papers}:
                person.papers.append(paper)


def careers_abroad(affiliations: Sequence[Mapping[str, Any]], limit: int = 3) -> list[str]:
    """Institutions outside Hong Kong on someone's record, longest-held first."""
    abroad = [
        (len(item.get("years") or []), item["institution"])
        for item in affiliations
        if (item.get("institution") or {}).get("country_code") not in (None, COUNTRY)
        and len(item.get("years") or []) >= MIN_YEARS_ABROAD
    ]
    abroad.sort(key=lambda pair: -pair[0])
    return [
        f"{institution['display_name']} ({institution['country_code']})"
        for _, institution in abroad[:limit]
    ]


def add_careers(candidates: Sequence[Candidate], client: httpx.Client) -> None:
    """Fills in ORCID, h-index and career outside Hong Kong from the authors' records."""
    by_id = {c.author_id: c for c in candidates}
    ids = list(by_id)
    for start in range(0, len(ids), 50):
        batch = ids[start : start + 50]
        response = client.get(
            f"{OPENALEX}/authors",
            params={
                "filter": f"openalex:{'|'.join(batch)}",
                "per-page": 50,
                "select": "id,display_name,orcid,affiliations,summary_stats",
            },
        )
        response.raise_for_status()
        for record in response.json().get("results", []):
            person = by_id.get(short_id(record["id"]))
            if person is None:
                continue
            person.orcid = record.get("orcid")
            person.h_index = (record.get("summary_stats") or {}).get("h_index")
            person.abroad = careers_abroad(record.get("affiliations") or [])


def rank(candidates: Iterable[Candidate], prefer_international: bool) -> list[Candidate]:
    def key(c: Candidate) -> tuple[bool, int, int]:
        international_first = prefer_international and not c.abroad
        return (international_first, -len(c.papers), -(c.h_index or 0))

    return sorted(candidates, key=key)


def find(
    topics: Mapping[str, Sequence[str]],
    since: date,
    prefer_international: bool = True,
    exclude: frozenset[str] = frozenset(),
    client: httpx.Client | None = None,
) -> list[Candidate]:
    """People in Hong Kong publishing on the topics since `since`, best first. One failing search
    is logged and skipped. A topic with no exact matches is searched again loosely."""
    own_client = client is None
    client = client or httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True)
    try:
        found: dict[str, Candidate] = {}
        for topic, phrases in topics.items():
            for phrase in phrases:
                try:
                    works = search_works(phrase, since, client)
                    if not works and len(topics) == 1:
                        works = search_works(phrase, since, client, exact=False)
                    add_works(found, works, topic)
                except httpx.HTTPError:
                    log.warning("OpenAlex search failed for %r; skipping it", phrase)
        fresh = [c for c in found.values() if c.author_id not in exclude]
        # Most matching papers first, so the career lookups go to the likeliest people.
        fresh.sort(key=lambda c: -len(c.papers))
        top = fresh[:ENRICH]
        try:
            add_careers(top, client)
        except httpx.HTTPError:
            log.warning("OpenAlex author lookup failed; suggesting without career records")
        return rank(top, prefer_international)
    finally:
        if own_client:
            client.close()


def about(candidate: Candidate, since: date) -> str:
    """One line on why this person is worth meeting."""
    papers = sorted(candidate.papers, key=lambda p: -(p.year or 0))
    count = len(papers)
    topics = "; ".join(sorted(candidate.topics))
    line = f"{count} paper{'s' if count != 1 else ''} since {since.year} ({topics})"
    if papers:
        year = f" ({papers[0].year})" if papers[0].year else ""
        line += f', latest "{papers[0].title}"{year}'
    if candidate.h_index:
        line += f" · h-index {candidate.h_index}"
    if candidate.abroad:
        line += f" · career outside Hong Kong: {', '.join(candidate.abroad)}"
    return line
