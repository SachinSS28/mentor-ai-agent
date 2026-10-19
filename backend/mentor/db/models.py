"""Core data model (blueprint Appendix C, plus workspaces, notion_sync and llm_usage).

Allowed values for status-like columns are enforced with CHECK constraints so the
database rejects bad data even if a caller skips validation.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Matches the embedding models shortlisted for G1 (Cohere Embed Multilingual v3, Titan v2).
EMBEDDING_DIMENSIONS = 1024

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def one_of(column: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=column)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID, primary_key=True, server_default=func.gen_random_uuid())


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def workspace_fk() -> Mapped[str | None]:
    return mapped_column(ForeignKey("workspaces.id"), index=True)


class Workspace(Base):
    """One business or client context; Mentor never mixes their details."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    about: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (
        one_of("level", ("quarter", "month", "week")),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    level: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    metric: Mapped[str | None] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="0")
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("goals.id"))
    workspace_id: Mapped[str | None] = workspace_fk()
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Task(Base):
    """A to-do. `ref` is the short T-number shown to the owner ("done T3"); `remind_at` sends a
    Telegram reminder once, and `reminded_at` records that it went."""

    __tablename__ = "tasks"
    __table_args__ = (
        one_of("status", ("open", "done", "cancelled")),
        one_of("source", ("text", "voice", "email", "meeting", "notion", "app")),
        one_of("created_by", ("owner", "mentor")),
        CheckConstraint("priority BETWEEN 1 AND 4", name="priority"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    ref: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    title: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    workspace_id: Mapped[str | None] = workspace_fk()
    goal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("goals.id"), index=True)
    due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    priority: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    status: Mapped[str] = mapped_column(String(16), server_default="open", index=True)
    source: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(16))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Routine(Base):
    __tablename__ = "routines"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True)
    schedule: Mapped[str] = mapped_column(Text)
    steps: Mapped[list[Any]] = mapped_column(server_default="[]")
    streak: Mapped[int] = mapped_column(Integer, server_default="0")
    last_done: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


CONTACT_STATUSES = (
    "suggested",  # found by Mentor, not acted on yet
    "to_contact",
    "invited",  # connection request sent
    "connected",
    "replied",
    "met",
    "not_now",
)


class Person(Base):
    """Someone in the owner's world. Networking contacts carry an `outreach` status; `ref` is the
    short P-number shown to the owner ("invited P12"). Mentor's suggestions come from OpenAlex."""

    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_people_source_external_id"),
        one_of("source", ("owner", "openalex")),
        one_of("outreach", CONTACT_STATUSES),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    ref: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    name: Mapped[str] = mapped_column(Text)
    relationship: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    workspace_id: Mapped[str | None] = workspace_fk()
    email: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), server_default="owner")
    external_id: Mapped[str | None] = mapped_column(Text)  # OpenAlex author id
    profile_url: Mapped[str | None] = mapped_column(Text)  # ORCID or OpenAlex page
    about: Mapped[str | None] = mapped_column(Text)  # why Mentor suggested them
    outreach: Mapped[str | None] = mapped_column(String(16), index=True)
    outreach_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # last change
    suggested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_on: Mapped[date | None] = mapped_column(Date)
    last_contact: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Place(Base):
    """Areas only, never exact addresses."""

    __tablename__ = "places"
    __table_args__ = (one_of("usual_mode", ("car", "local_train", "metro", "cab", "walk")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    label: Mapped[str] = mapped_column(Text, unique=True)
    area: Mapped[str] = mapped_column(Text)
    usual_mode: Mapped[str] = mapped_column(String(16))
    buffer_minutes: Mapped[int] = mapped_column(Integer, server_default="15")
    created_at: Mapped[datetime] = created_at()


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = uuid_pk()
    calendar_event_id: Mapped[str] = mapped_column(Text, unique=True)
    workspace_id: Mapped[str | None] = workspace_fk()
    title: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    brief: Mapped[str | None] = mapped_column(Text)
    transcript_ref: Mapped[str | None] = mapped_column(Text)  # path on the Mac; audio never syncs
    summary: Mapped[str | None] = mapped_column(Text)
    decisions: Mapped[list[Any]] = mapped_column(server_default="[]")
    actions: Mapped[list[Any]] = mapped_column(server_default="[]")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (one_of("status", ("planning", "booked", "active", "done", "cancelled")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="planning")
    legs: Mapped[list[Any]] = mapped_column(server_default="[]")
    pnrs: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    offline_pack_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[uuid.UUID] = uuid_pk()
    spent_on: Mapped[date] = mapped_column(Date, index=True)
    vendor: Mapped[str] = mapped_column(Text)
    gstin: Mapped[str | None] = mapped_column(String(15))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), server_default="INR")
    tax_split: Mapped[dict[str, Any]] = mapped_column(server_default="{}")  # cgst / sgst / igst
    trip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trips.id"))
    workspace_id: Mapped[str | None] = workspace_fk()
    receipt_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class Memory(Base):
    """Facts Mentor was told. Items marked private never go to a cloud model."""

    __tablename__ = "memories"
    __table_args__ = (
        one_of("layer", ("profile", "preference", "workspace", "people", "goal", "episodic")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    layer: Mapped[str] = mapped_column(String(16), index=True)
    fact: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    workspace_id: Mapped[str | None] = workspace_fk()
    private_flag: Mapped[bool] = mapped_column(server_default="false")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    # Forgetting marks the row instead of deleting it, so the Mac and the cloud copy of the
    # database can pass the deletion on to each other (mentor.db.sync).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Approval(Base):
    """A T2 action waiting for you. Sentinel executes the stored payload, never a new one."""

    __tablename__ = "approvals"
    __table_args__ = (
        one_of("tier", ("T2",)),
        one_of("status", ("pending", "approved", "rejected", "expired", "executed")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tool: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column()
    payload_hash: Mapped[str] = mapped_column(String(64))
    preview: Mapped[str] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(16), server_default="pending", index=True)
    user_request: Mapped[str | None] = mapped_column(Text)  # used by the intent check
    thread_id: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        one_of("tier", ("T0", "T1", "T2", "T3")),
        one_of("status", ("ok", "blocked", "error", "pending_approval")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    tool: Mapped[str] = mapped_column(Text, index=True)
    tier: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(20))
    args_redacted: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    args_hash: Mapped[str] = mapped_column(String(64))
    result_summary: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))


class NotionSync(Base):
    """Links a Mentor record to its Notion page for two-way sync."""

    __tablename__ = "notion_sync"
    __table_args__ = (one_of("entity_type", ("task", "goal")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID, unique=True)
    notion_page_id: Mapped[str] = mapped_column(Text, unique=True)
    notion_last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_version: Mapped[int] = mapped_column(Integer, server_default="0")


class LlmUsage(Base):
    """One row per model call; Sentinel sums these against the monthly budget cap."""

    __tablename__ = "llm_usage"
    __table_args__ = (one_of("role", ("orchestrator", "fast", "bulk", "embed")),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), server_default="0")
    thread_id: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(Text)


class Heartbeat(Base):
    """When each copy of the Telegram bot was last alive; the Mac's standby reads the cloud's."""

    __tablename__ = "heartbeats"
    __table_args__ = (one_of("host", ("mac", "cloud")),)

    host: Mapped[str] = mapped_column(String(8), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


JOB_STATUSES = (
    "new",  # found in an alert, not shown yet
    "shortlisted",  # shown in a digest
    "saved",
    "skipped",
    "applied",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
)


class Job(Base):
    """A job found in a job-alert email (LinkedIn so far). `ref` is the short J-number shown to
    the owner ("save J12"); `fit_score` is 0-100 against the owner's profile."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
        one_of("source", ("linkedin",)),
        one_of("status", JOB_STATUSES),
        CheckConstraint("fit_score BETWEEN 0 AND 100", name="fit_score"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    ref: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    source: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    insights: Mapped[list[Any]] = mapped_column(server_default="[]")  # e.g. "1 school alum"
    alert: Mapped[str | None] = mapped_column(Text)  # the saved search that found it
    email_id: Mapped[str] = mapped_column(Text)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fit_score: Mapped[int | None] = mapped_column(SmallInteger)
    fit_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="new", index=True)
    shortlisted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ProcessedEmail(Base):
    """Job-alert emails already read, so each is parsed once."""

    __tablename__ = "processed_emails"

    email_id: Mapped[str] = mapped_column(Text, primary_key=True)
    jobs_found: Mapped[int] = mapped_column(Integer)
    processed_at: Mapped[datetime] = created_at()


class AppState(Base):
    """Small facts Mentor keeps between restarts, e.g. the date of the last job digest."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = updated_at()


BRIEFING_KINDS = ("paper", "journal", "news", "funding")


class BriefingItem(Base):
    """A paper, journal article, news story or funding call for the morning briefing. `score` is
    0-100 relevance to the owner's profile; `why` says what's new and why it matters to them."""

    __tablename__ = "briefing_items"
    __table_args__ = (
        one_of("kind", BRIEFING_KINDS),
        one_of("status", ("new", "sent")),
        CheckConstraint("score BETWEEN 0 AND 100", name="score"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(Text)  # journal or feed name
    topic: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, unique=True)
    snippet: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[int | None] = mapped_column(SmallInteger)
    why: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), server_default="new", index=True)
    found_at: Mapped[datetime] = created_at()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchHighlight(Base):
    """A paper sent in the twice-weekly research highlights: one of the most-cited recent papers
    in the owner's topics ("cited") or a new paper in a high-impact journal ("top_journal")."""

    __tablename__ = "research_highlights"
    __table_args__ = (one_of("kind", ("cited", "top_journal")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    work_id: Mapped[str] = mapped_column(Text, unique=True)  # OpenAlex id
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    journal: Mapped[str | None] = mapped_column(Text)
    published_on: Mapped[date | None] = mapped_column(Date)
    citations: Mapped[int] = mapped_column(Integer, server_default="0")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
