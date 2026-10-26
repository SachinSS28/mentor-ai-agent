"""Scholarly search and reading. OpenAlex supplies papers, citations, related work and
open-access full text (it carries Unpaywall's open-access links). Semantic Scholar adds a
one-line summary (TLDR) when it answers; without an API key it is often busy, so it is optional.
"""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from mentor.briefing.sources import abstract_text, clean
from mentor.reading import read_pdf
from mentor.settings import get_settings

log = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR = "https://api.semanticscholar.org/graph/v1/paper"
HEADERS = {"User-Agent": "Mentor/1.0 (personal research assistant)"}
FIELDS = (
    "id,doi,title,publication_year,cited_by_count,fwci,primary_location,best_oa_location,"
    "open_access,authorships,abstract_inverted_index,related_works,referenced_works"
)
DOI = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
WORK_ID = re.compile(r"^W\d+$", re.IGNORECASE)
MAX_PDF_BYTES = 25_000_000
TEXT_CHARS = 10000  # about 2,500 tokens of a paper per read
WORD = re.compile(r"[a-z0-9]{4,}")


@dataclass
class Paper:
    id: str  # OpenAlex id, e.g. "W4391234567"
    title: str
    year: int | None
    venue: str | None
    citations: int
    doi: str | None = None
    fwci: float | None = None  # field-weighted citation impact: 1.0 is the world average
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    pdf_url: str | None = None
    landing_url: str | None = None
    related: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)

    @property
    def link(self) -> str:
        return self.doi or self.landing_url or f"https://openalex.org/{self.id}"


def short_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def parse(work: dict[str, Any]) -> Paper:
    source = (work.get("primary_location") or {}).get("source") or {}
    best = work.get("best_oa_location") or {}
    return Paper(
        id=short_id(work["id"]),
        title=clean(work.get("title") or "(untitled)", 300),
        year=work.get("publication_year"),
        venue=source.get("display_name"),
        citations=int(work.get("cited_by_count") or 0),
        doi=work.get("doi"),
        fwci=work.get("fwci"),
        authors=[
            a["author"]["display_name"]
            for a in (work.get("authorships") or [])[:4]
            if (a.get("author") or {}).get("display_name")
        ],
        abstract=abstract_text(work.get("abstract_inverted_index"), 1500),
        pdf_url=best.get("pdf_url"),
        landing_url=best.get("landing_page_url"),
        related=[short_id(w) for w in work.get("related_works") or []],
        references=[short_id(w) for w in work.get("referenced_works") or []],
    )


def client() -> httpx.Client:
    return httpx.Client(timeout=40, headers=HEADERS, follow_redirects=True)


@contextmanager
def connection(http: httpx.Client | None) -> Iterator[httpx.Client]:
    """The caller's client, left open, or a new one closed afterwards."""
    if http is not None:
        yield http
        return
    with client() as own:
        yield own


def search(
    query: str, since_year: int = 0, limit: int = 8, http: httpx.Client | None = None
) -> list[Paper]:
    """Journal articles and reviews matching the query, most relevant first."""
    filters = ["type:article|review"]
    if since_year:
        filters.append(f"from_publication_date:{since_year}-01-01")
    params: dict[str, str | int] = {
        "search": query.replace(",", " "),
        "filter": ",".join(filters),
        "per-page": max(1, min(limit, 25)),
        "select": FIELDS,
    }
    with connection(http) as c:
        response = c.get(OPENALEX, params=params)
        response.raise_for_status()
        return [parse(w) for w in response.json().get("results", [])]


def find(reference: str, http: httpx.Client | None = None) -> Paper | None:
    """A paper by DOI, OpenAlex id (W...), or title words."""
    text = reference.strip()
    with connection(http) as c:
        if doi := DOI.search(text):
            url = f"{OPENALEX}/doi:{doi.group(0).rstrip('.,;)')}"
        elif WORK_ID.match(text):
            url = f"{OPENALEX}/{text.upper()}"
        else:
            found = search(text, limit=1, http=c)
            return found[0] if found else None
        response = c.get(url, params={"select": FIELDS})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return parse(response.json())


def most_cited(ids: list[str], limit: int = 5, http: httpx.Client | None = None) -> list[Paper]:
    """The most-cited papers among these OpenAlex ids (e.g. a paper's references)."""
    if not ids:
        return []
    with connection(http) as c:
        response = c.get(
            OPENALEX,
            params={
                "filter": f"openalex:{'|'.join(ids[:50])}",
                "sort": "cited_by_count:desc",
                "per-page": limit,
                "select": "id,doi,title,publication_year,cited_by_count,primary_location",
            },
        )
        response.raise_for_status()
        return [parse(w) for w in response.json().get("results", [])]


def tldr(paper: Paper) -> str | None:
    """Semantic Scholar's one-line summary, or None when it's busy or has none."""
    if not paper.doi:
        return None
    key = get_settings().semantic_scholar_api_key
    headers = {"x-api-key": key.get_secret_value()} if key else {}
    doi = paper.doi.removeprefix("https://doi.org/")
    try:
        response = httpx.get(
            f"{SEMANTIC_SCHOLAR}/DOI:{doi}", params={"fields": "tldr"}, headers=headers, timeout=15
        )
        if response.status_code != 200:
            return None
        return ((response.json().get("tldr") or {}).get("text")) or None
    except httpx.HTTPError:
        return None


def full_text(paper: Paper, http: httpx.Client | None = None) -> str | None:
    """The open-access PDF's text, or None when there is no open-access PDF."""
    if not paper.pdf_url:
        return None
    with connection(http) as c:
        try:
            response = c.get(paper.pdf_url)
        except httpx.HTTPError:
            return None
        data = response.content
        if response.status_code != 200 or len(data) > MAX_PDF_BYTES or not data.startswith(b"%PDF"):
            return None  # often a landing page, not the PDF itself
    return read_pdf(data)


def focus(text: str, question: str = "", limit: int = TEXT_CHARS) -> str:
    """The part of a long paper worth reading: for a question, the paragraphs sharing most words
    with it (in their original order); otherwise the start and the end (findings, conclusions)."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= limit:
        return text
    words = set(WORD.findall(question.lower()))
    if not words:
        head = text[: limit * 6 // 10].rsplit(" ", 1)[0]
        tail = text[-(limit * 4 // 10) :].split(" ", 1)[-1]
        return f"{head}\n[…]\n{tail}"
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z0-9])", text) if p.strip()]
    ranked = sorted(
        range(len(paragraphs)),
        key=lambda i: -len(words & set(WORD.findall(paragraphs[i].lower()))),
    )
    chosen, size = set(), 0
    for i in ranked:
        if size + len(paragraphs[i]) > limit:
            continue
        chosen.add(i)
        size += len(paragraphs[i])
    return "\n[…]\n".join(paragraphs[i] for i in sorted(chosen))


def line(paper: Paper) -> str:
    details = [str(paper.year) if paper.year else None, paper.venue, f"{paper.citations} citations"]
    if paper.fwci is not None:
        details.append(f"FWCI {paper.fwci:.1f}")
    if paper.pdf_url:
        details.append("open access")
    shown = " · ".join(d for d in details if d)
    return f"- {paper.id} · {paper.title} ({shown}) {paper.link}"
