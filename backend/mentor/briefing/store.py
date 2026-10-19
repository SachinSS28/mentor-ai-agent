"""Briefing items in Postgres: each item once (by link), its score, and whether it was sent."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from mentor.briefing.sources import Item
from mentor.db.models import BriefingItem
from mentor.db.session import get_sessionmaker


def save(items: Sequence[Item]) -> int:
    """Store items not seen before. Returns how many were new."""
    new = 0
    with get_sessionmaker()() as session:
        for item in items:
            inserted = session.execute(
                insert(BriefingItem)
                .values(
                    kind=item.kind,
                    source=item.source,
                    topic=item.topic,
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    published_at=item.published_at,
                )
                .on_conflict_do_nothing(index_elements=[BriefingItem.url])
                .returning(BriefingItem.id)
            ).first()
            new += inserted is not None
        session.commit()
    return new


def unscored(limit: int) -> list[BriefingItem]:
    query = (
        select(BriefingItem)
        .where(BriefingItem.score.is_(None))
        .order_by(BriefingItem.found_at.desc())
        .limit(limit)
    )
    with get_sessionmaker()() as session:
        return list(session.scalars(query))


def set_score(item_id: uuid.UUID, score: int, why: str) -> None:
    with get_sessionmaker()() as session:
        session.execute(
            update(BriefingItem)
            .where(BriefingItem.id == item_id)
            .values(score=max(0, min(score, 100)), why=why.strip()[:300])
        )
        session.commit()


def best(kind: str, min_score: int, limit: int, since: datetime) -> list[BriefingItem]:
    """Unsent items of one kind, found since `since`, most relevant first."""
    query = (
        select(BriefingItem)
        .where(
            BriefingItem.kind == kind,
            BriefingItem.status == "new",
            BriefingItem.score >= min_score,
            BriefingItem.found_at >= since,
        )
        .order_by(BriefingItem.score.desc(), BriefingItem.published_at.desc().nulls_last())
        .limit(limit)
    )
    with get_sessionmaker()() as session:
        return list(session.scalars(query))


def mark_sent(ids: Sequence[uuid.UUID], now: datetime) -> None:
    with get_sessionmaker()() as session:
        session.execute(
            update(BriefingItem).where(BriefingItem.id.in_(ids)).values(status="sent", sent_at=now)
        )
        session.commit()


def search(
    query: str, kind: str, since: datetime, min_score: int, limit: int
) -> list[BriefingItem]:
    stmt = select(BriefingItem).where(
        BriefingItem.found_at >= since, BriefingItem.score >= min_score
    )
    for word in query.split():
        pattern = f"%{word}%"
        stmt = stmt.where(
            or_(
                BriefingItem.title.ilike(pattern),
                BriefingItem.snippet.ilike(pattern),
                BriefingItem.topic.ilike(pattern),
                BriefingItem.source.ilike(pattern),
            )
        )
    if kind:
        stmt = stmt.where(BriefingItem.kind == kind)
    stmt = stmt.order_by(BriefingItem.score.desc(), BriefingItem.found_at.desc()).limit(limit)
    with get_sessionmaker()() as session:
        return list(session.scalars(stmt))
