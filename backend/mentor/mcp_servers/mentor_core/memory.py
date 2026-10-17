"""Long-term memory: lasting facts the owner told Mentor (plan step 1.6, ahead of Sentinel).

Recall matches words for now; pgvector search comes with the embeddings model (G1).
"""

import re
import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import func, select

from mentor.db.models import Memory
from mentor.db.session import get_sessionmaker

Layer = Literal["profile", "preference", "workspace", "people", "goal", "episodic"]

# About 10,000 tokens per lookup: a whole CV plus a page of other facts.
RECALL_BUDGET_CHARS = 40_000

WORD = re.compile(r"\w{2,}")  # two letters, so "CV" counts
COMMON_WORDS = (
    "about an and are at be did do does for from has have how in is it me my of on or so "
    "that the this to was what when where who why with you your"
)
COMMON = frozenset(COMMON_WORDS.split())


def words(text: str) -> set[str]:
    return {word for word in WORD.findall(text.lower()) if word not in COMMON}


def remember(
    fact: str,
    layer: Layer = "profile",
    *,
    private: bool = False,
    replaces: uuid.UUID | None = None,
    source: str | None = None,
) -> Memory:
    """Save a fact, or overwrite the memory `replaces` with a correction."""
    with get_sessionmaker()() as session, session.begin():
        if replaces is None:
            memory = Memory(layer=layer, fact=fact, private_flag=private, source=source)
            session.add(memory)
        else:
            found = session.get(Memory, replaces)
            if found is None or found.deleted_at is not None:
                raise LookupError(f"There is no saved memory {replaces}.")
            memory = found
            memory.layer, memory.fact, memory.private_flag = layer, fact, private
            memory.source = source or memory.source
    return memory


def rank(memories: Sequence[Memory], query: str, budget: int = RECALL_BUDGET_CHARS) -> list[Memory]:
    """The memories sharing most words with the query (then the newest) that fit the budget,
    oldest first, so a document saved in parts reads in order."""
    wanted = words(query)

    def relevance(memory: Memory) -> int:
        return len(wanted & words(memory.fact))

    ordered = sorted(memories, key=lambda m: (relevance(m), m.updated_at), reverse=True)
    chosen: list[Memory] = []
    used = 0
    for memory in ordered:
        if chosen and used + len(memory.fact) > budget:
            break
        chosen.append(memory)
        used += len(memory.fact)
    return sorted(chosen, key=lambda m: m.updated_at)


def recall(query: str = "", *, include_private: bool) -> list[Memory]:
    statement = select(Memory).where(Memory.deleted_at.is_(None))
    if not include_private:
        statement = statement.where(Memory.private_flag.is_(False))
    with get_sessionmaker()() as session:
        return rank(session.scalars(statement).all(), query)


def forget(memory_id: uuid.UUID) -> Memory | None:
    with get_sessionmaker()() as session, session.begin():
        memory = session.get(Memory, memory_id)
        if memory is None or memory.deleted_at is not None:
            return None
        memory.deleted_at = func.now()
    return memory
