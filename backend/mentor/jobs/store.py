"""The Job Desk's records in Postgres: jobs, processed alert emails, and small saved state."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from mentor.db.models import JOB_STATUSES, Job, ProcessedEmail
from mentor.db.session import get_sessionmaker
from mentor.jobs.alerts import Posting

# Statuses the owner sets; "new" and "shortlisted" are Mentor's own.
OWNER_STATUSES = tuple(s for s in JOB_STATUSES if s not in ("new", "shortlisted"))


def is_processed(email_id: str) -> bool:
    with get_sessionmaker()() as session:
        return session.get(ProcessedEmail, email_id) is not None


def save_postings(postings: Sequence[Posting], email_id: str, found_at: datetime) -> int:
    """Store the jobs from one alert email (each job once, however many alerts list it) and mark
    the email as read. Returns how many jobs were new."""
    with get_sessionmaker()() as session:
        new = 0
        for posting in postings:
            inserted = session.execute(
                insert(Job)
                .values(
                    source="linkedin",
                    external_id=posting.external_id,
                    title=posting.title,
                    company=posting.company,
                    location=posting.location,
                    url=posting.url,
                    insights=list(posting.insights),
                    alert=posting.alert,
                    email_id=email_id,
                    found_at=found_at,
                )
                .on_conflict_do_nothing(constraint="uq_jobs_source_external_id")
                .returning(Job.id)
            ).first()
            new += inserted is not None
        session.add(ProcessedEmail(email_id=email_id, jobs_found=len(postings)))
        session.commit()
    return new


def save_searched(postings: Sequence[Posting], found_at: datetime) -> tuple[list[Job], int]:
    """Store jobs from a LinkedIn search. A job already on the desk (from an alert or an earlier
    search) keeps its J-number and status. Returns the jobs in search order, and how many were
    new."""
    if not postings:
        return [], 0
    ids = [posting.external_id for posting in postings]
    on_desk = select(Job.external_id).where(Job.source == "linkedin", Job.external_id.in_(ids))
    with get_sessionmaker()() as session:
        # Known jobs aren't offered to the insert at all: each attempt would use up a J-number.
        known = set(session.scalars(on_desk))
        new = 0
        for posting in postings:
            if posting.external_id in known:
                continue
            known.add(posting.external_id)
            inserted = session.execute(
                insert(Job)
                .values(
                    source="linkedin",
                    external_id=posting.external_id,
                    title=posting.title,
                    company=posting.company,
                    location=posting.location,
                    url=posting.url,
                    insights=list(posting.insights),
                    alert=posting.alert,
                    found_at=found_at,
                )
                .on_conflict_do_nothing(constraint="uq_jobs_source_external_id")
                .returning(Job.id)
            ).first()
            new += inserted is not None
        session.commit()
        query = select(Job).where(Job.source == "linkedin", Job.external_id.in_(ids))
        found = {job.external_id: job for job in session.scalars(query)}
    return [found[i] for i in dict.fromkeys(ids) if i in found], new


def unscored(limit: int) -> list[Job]:
    query = select(Job).where(Job.fit_score.is_(None)).order_by(Job.found_at.desc()).limit(limit)
    with get_sessionmaker()() as session:
        return list(session.scalars(query))


def set_score(ref: int, score: int, reason: str) -> None:
    with get_sessionmaker()() as session:
        session.execute(
            update(Job)
            .where(Job.ref == ref)
            .values(fit_score=max(0, min(score, 100)), fit_reason=reason.strip()[:300])
        )
        session.commit()


def parse_ref(text: str) -> int:
    """'J12', 'j12', '#12' or '12' -> 12."""
    digits = text.strip().lstrip("#").lstrip("Jj")
    if not digits.isdigit():
        raise ValueError(f"{text!r} is not a job number like J12")
    return int(digits)


def by_ref(ref: int) -> Job | None:
    with get_sessionmaker()() as session:
        return session.scalars(select(Job).where(Job.ref == ref)).first()


def find(query: str = "", statuses: Sequence[str] = (), limit: int = 10) -> list[Job]:
    """Jobs matching every word of `query` (title, company, location), best fit first."""
    stmt = select(Job)
    for word in query.split():
        pattern = f"%{word}%"
        stmt = stmt.where(
            or_(Job.title.ilike(pattern), Job.company.ilike(pattern), Job.location.ilike(pattern))
        )
    if statuses:
        stmt = stmt.where(Job.status.in_(statuses))
    stmt = stmt.order_by(Job.fit_score.desc().nulls_last(), Job.found_at.desc()).limit(limit)
    with get_sessionmaker()() as session:
        return list(session.scalars(stmt))


def set_status(ref: int, status: str, notes: str, follow_up_days: int, now: datetime) -> Job:
    if status not in OWNER_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(OWNER_STATUSES)}")
    values: dict[str, object] = {"status": status}
    if notes.strip():
        values["notes"] = notes.strip()
    if status == "applied":
        values["applied_at"] = now
        values["follow_up_on"] = now.date() + timedelta(days=follow_up_days)
    elif status in ("interview", "offer", "rejected", "withdrawn"):
        values["follow_up_on"] = None  # the follow-up reminder has served its purpose
    with get_sessionmaker()() as session:
        job = session.scalars(select(Job).where(Job.ref == ref)).first()
        if job is None:
            raise LookupError(f"There is no job J{ref}")
        session.execute(update(Job).where(Job.ref == ref).values(**values))
        session.commit()
        session.refresh(job)
        return job


def digest_candidates(min_score: int, limit: int) -> list[Job]:
    """New, scored jobs good enough for the shortlist, best first."""
    query = (
        select(Job)
        .where(Job.status == "new", Job.fit_score >= min_score)
        .order_by(Job.fit_score.desc(), Job.found_at.desc())
        .limit(limit)
    )
    with get_sessionmaker()() as session:
        return list(session.scalars(query))


def mark_shortlisted(refs: Sequence[int], now: datetime) -> None:
    with get_sessionmaker()() as session:
        session.execute(
            update(Job)
            .where(Job.ref.in_(refs), Job.status == "new")
            .values(status="shortlisted", shortlisted_at=now)
        )
        session.commit()


def follow_ups_due(today: date) -> list[Job]:
    query = (
        select(Job)
        .where(Job.status == "applied", Job.follow_up_on <= today)
        .order_by(Job.follow_up_on)
    )
    with get_sessionmaker()() as session:
        return list(session.scalars(query))


def applied_between(start: datetime, end: datetime) -> list[Job]:
    query = select(Job).where(Job.applied_at >= start, Job.applied_at < end)
    with get_sessionmaker()() as session:
        return list(session.scalars(query.order_by(Job.applied_at)))


def counts() -> dict[str, int]:
    with get_sessionmaker()() as session:
        rows = session.execute(select(Job.status, func.count()).group_by(Job.status))
        return {status: n for status, n in rows}


def search_text(job: Job) -> str:
    """Everything the model needs about a job, on one line."""
    parts = [f"J{job.ref}", job.title, job.company, job.location or "location not given"]
    if job.insights:
        parts.append("; ".join(str(i) for i in job.insights))
    return " | ".join(parts)
