"""Networking contacts in Postgres (the people table): Mentor's suggestions and the owner's
outreach, each with a P-number."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from mentor.db.models import CONTACT_STATUSES, Person
from mentor.db.session import get_sessionmaker
from mentor.network.experts import Candidate, about

# Statuses the owner sets; "suggested" is Mentor's own.
OWNER_STATUSES = tuple(s for s in CONTACT_STATUSES if s != "suggested")
IN_TOUCH = ("connected", "replied", "met")


def known_ids() -> frozenset[str]:
    """OpenAlex authors already suggested or tracked, so weekly suggestions are always new."""
    query = select(Person.external_id).where(Person.source == "openalex")
    with get_sessionmaker()() as session:
        return frozenset(author for author in session.scalars(query) if author)


def save_suggestions(candidates: Sequence[Candidate], since: date, now: datetime) -> list[Person]:
    """Stores the candidates as suggested contacts (or refreshes what Mentor knows about them,
    keeping any status the owner set). Returns them in order, with their P-numbers."""
    people = []
    with get_sessionmaker()() as session:
        for candidate in candidates:
            stmt = insert(Person).values(
                source="openalex",
                external_id=candidate.author_id,
                name=candidate.name,
                organization=candidate.organization,
                profile_url=candidate.profile_url,
                about=about(candidate, since),
                outreach="suggested",
                suggested_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_people_source_external_id",
                set_={
                    "organization": stmt.excluded.organization,
                    "profile_url": stmt.excluded.profile_url,
                    "about": stmt.excluded.about,
                },
            )
            people.append(session.scalars(stmt.returning(Person)).one())
        session.commit()
    return people


def parse_ref(text: str) -> int | None:
    """'P12', 'p12', '#12' or '12' -> 12; anything else (a name) -> None."""
    digits = text.strip().lstrip("#").lstrip("Pp")
    return int(digits) if digits.isdigit() else None


def find(query: str = "", statuses: Sequence[str] = (), limit: int = 15) -> list[Person]:
    """Contacts matching every word of `query` (name, organization, why suggested, notes),
    most recently changed first."""
    stmt = select(Person).where(Person.outreach.is_not(None))
    for word in query.split():
        pattern = f"%{word}%"
        stmt = stmt.where(
            or_(
                Person.name.ilike(pattern),
                Person.organization.ilike(pattern),
                Person.about.ilike(pattern),
                Person.notes.ilike(pattern),
            )
        )
    if statuses:
        stmt = stmt.where(Person.outreach.in_(statuses))
    stmt = stmt.order_by(Person.updated_at.desc(), Person.ref.desc()).limit(limit)
    with get_sessionmaker()() as session:
        return list(session.scalars(stmt))


def normalize_status(status: str) -> str:
    """'Not now' -> 'not_now'."""
    return "_".join(status.strip().lower().replace("-", " ").split())


def track(
    person: str,
    status: str,
    notes: str,
    organization: str,
    follow_up_days: int,
    now: datetime,
) -> tuple[Person, bool]:
    """Records outreach with a contact, found by P-number or name; someone new is added.
    Returns the contact and whether they were added."""
    status = normalize_status(status)
    if status and status not in OWNER_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(OWNER_STATUSES)}")
    ref = parse_ref(person)
    with get_sessionmaker()() as session:
        if ref is not None:
            contact = session.scalars(select(Person).where(Person.ref == ref)).first()
            if contact is None:
                raise LookupError(f"There is no contact P{ref}")
        else:
            name = " ".join(person.split())
            if not name:
                raise ValueError("give a P-number or the person's name")
            contact = session.scalars(
                select(Person).where(func.lower(Person.name) == name.lower())
            ).first()
        added = contact is None
        if contact is None:
            contact = Person(name=name, source="owner", outreach="to_contact")
            session.add(contact)
        if status:
            contact.outreach = status
            if status == "invited":
                contact.follow_up_on = now.date() + timedelta(days=follow_up_days)
            else:
                contact.follow_up_on = None
            if status in IN_TOUCH:
                contact.last_contact = now.date()
        elif contact.outreach in (None, "suggested"):
            contact.outreach = "to_contact"  # the owner took an interest
        if organization.strip():
            contact.organization = organization.strip()
        if notes.strip():
            entry = f"{now:%d %b}: {notes.strip()}"
            contact.notes = f"{contact.notes}\n{entry}" if contact.notes else entry
        session.commit()
        session.refresh(contact)
        return contact, added


def follow_ups_due(today: date) -> list[Person]:
    """Connection requests sent a while ago, to ask whether they were accepted."""
    query = (
        select(Person)
        .where(Person.outreach == "invited", Person.follow_up_on <= today)
        .order_by(Person.follow_up_on)
    )
    with get_sessionmaker()() as session:
        return list(session.scalars(query))


def snooze(refs: Sequence[int], until: date) -> None:
    """Asks again on `until` rather than every morning."""
    with get_sessionmaker()() as session:
        session.execute(update(Person).where(Person.ref.in_(refs)).values(follow_up_on=until))
        session.commit()


def counts() -> dict[str, int]:
    query = (
        select(Person.outreach, func.count())
        .where(Person.outreach.is_not(None))
        .group_by(Person.outreach)
    )
    with get_sessionmaker()() as session:
        return {status: n for status, n in session.execute(query) if status}
