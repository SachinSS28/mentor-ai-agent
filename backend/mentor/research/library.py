"""The owner's library: documents in the Mentor folder on Google Drive and Mentor's research
reports, split into passages and embedded on the Mac (EmbeddingGemma via Ollama), so Mentor can
search them by meaning rather than exact words."""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from mentor.agent.models import get_embeddings
from mentor.charter import get_charter
from mentor.db.models import LibraryChunk
from mentor.db.session import get_sessionmaker
from mentor.db.state import get_state, set_state
from mentor.mcp_servers.google_tools import drive

log = logging.getLogger(__name__)

CHUNK_CHARS = 1200  # well inside EmbeddingGemma's 2,048-token window
OVERLAP = 200  # carried into the next passage, so a sentence cut in two is still found
SYNC_EVERY = timedelta(hours=3)
SYNCED_AT = "library.synced_at"
# EmbeddingGemma was trained with these prompts for search.
QUERY_PROMPT = "task: search result | query: {}"
DOCUMENT_PROMPT = "title: {} | text: {}"


@dataclass(frozen=True)
class Passage:
    title: str
    source: str
    text: str
    distance: float  # cosine distance: 0 is identical, under about 0.5 is a good match


def chunks(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Passages of about `size` characters, broken between paragraphs or sentences."""
    pieces = [p.strip() for p in re.split(r"\n\s*\n|(?<=[.!?])\s+", text) if p.strip()]
    passages: list[str] = []
    current = ""
    for piece in pieces:
        while len(piece) > size:  # one huge paragraph or table
            passages.append(piece[:size])
            piece = piece[size - overlap :]
        if current and len(current) + len(piece) + 1 > size:
            passages.append(current)
            current = current[-overlap:].split(" ", 1)[-1]
        current = f"{current} {piece}".strip()
    if current:
        passages.append(current)
    return passages


def replace(source: str, source_id: str, title: str, version: str, text: str) -> int:
    """(Re)indexes one document. Returns how many passages it has."""
    passages = chunks(text)
    vectors = (
        get_embeddings().embed_documents([DOCUMENT_PROMPT.format(title, p) for p in passages])
        if passages
        else []
    )
    with get_sessionmaker()() as session:
        session.execute(
            delete(LibraryChunk).where(
                LibraryChunk.source == source, LibraryChunk.source_id == source_id
            )
        )
        session.add_all(
            LibraryChunk(
                source=source,
                source_id=source_id,
                title=title,
                version=version,
                chunk=n,
                text=passage,
                embedding=vector,
            )
            for n, (passage, vector) in enumerate(zip(passages, vectors, strict=True))
        )
        session.commit()
    return len(passages)


def versions(source: str) -> dict[str, str]:
    query = select(LibraryChunk.source_id, LibraryChunk.version).where(
        LibraryChunk.source == source
    )
    with get_sessionmaker()() as session:
        return {source_id: version for source_id, version in session.execute(query.distinct())}


def forget(source: str, keep: Sequence[str]) -> None:
    with get_sessionmaker()() as session:
        session.execute(
            delete(LibraryChunk).where(
                LibraryChunk.source == source, LibraryChunk.source_id.not_in(keep)
            )
        )
        session.commit()


def sync_drive() -> int:
    """Indexes new and changed documents in the Drive folder and drops deleted ones. Returns
    how many documents were (re)indexed."""
    service = drive.drive_service()
    files = drive.list_files(service, get_charter().documents.drive_folder)
    known = versions("drive")
    done = 0
    for f in files:
        if known.get(f.id) == f.modified:
            continue
        try:
            replace("drive", f.id, f.name, f.modified, drive.text_of(service, f))
            done += 1
        except Exception:
            log.exception("Couldn't index %s; trying again at the next sync", f.name)
    forget("drive", [f.id for f in files])
    return done


def tick(now: datetime) -> None:
    """Keeps the library in step with the Drive folder, every SYNC_EVERY."""
    last = get_state(SYNCED_AT)
    if last and now - datetime.fromisoformat(last) < SYNC_EVERY:
        return
    indexed = sync_drive()
    set_state(SYNCED_AT, now.isoformat())
    if indexed:
        log.info("Library: indexed %d new or changed documents", indexed)


def index_report(ref: int, question: str, report: str) -> None:
    replace("report", str(ref), f"Research report R{ref}: {question}", "1", report)


def search(query: str, limit: int = 5, per_document: int = 2) -> list[Passage]:
    vector = get_embeddings().embed_query(QUERY_PROMPT.format(query))
    distance = LibraryChunk.embedding.cosine_distance(vector).label("distance")
    stmt = select(LibraryChunk, distance).order_by(distance).limit(limit * 4)
    with get_sessionmaker()() as session:
        rows = session.execute(stmt).all()
    found: list[Passage] = []
    seen: dict[str, int] = {}
    for chunk, dist in rows:
        key = f"{chunk.source}:{chunk.source_id}"
        if seen.get(key, 0) >= per_document:
            continue  # room for other documents
        seen[key] = seen.get(key, 0) + 1
        found.append(Passage(chunk.title, chunk.source, chunk.text, float(dist)))
        if len(found) == limit:
            break
    return found


def size() -> tuple[int, int]:
    """(documents, passages) in the library."""
    with get_sessionmaker()() as session:
        documents = session.scalar(
            select(func.count(func.distinct(LibraryChunk.source + ":" + LibraryChunk.source_id)))
        )
        passages = session.scalar(select(func.count()).select_from(LibraryChunk))
    return int(documents or 0), int(passages or 0)
