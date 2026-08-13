"""
SQLAlchemy models for the AI claims-denial triage + appeal-drafting pipeline.

Schema overview
---------------
denials         - one row per ingested denial letter (the "root" entity;
                  the only table an external system might reference, hence uuid pk)
extractions     - structured fields pulled from a denial by the LLM extraction step
classifications - denial-reason category assigned by the LLM classification step
appeals         - drafted appeal letters and their human-review status
corrections     - immutable append-only audit-event log: human edits to any
                  AI-produced field, AND appeal approve/reject/sent review
                  decisions (event_type distinguishes the two; see AuditEvent)
eval_runs       - results of a later phase's eval harness, tied to a git commit
token_usage     - per-call LLM token/cost accounting, for cost tracking

All internal "detail" tables use a plain identity (bigint) primary key and a
foreign key to denials.id. denials.status and denials.source_company are
indexed because the (not-yet-built) frontend will filter worklists on both.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


DENIAL_STATUSES = (
    "new",
    "processing",
    "classified",
    "appeal_drafted",
    "needs_review",
    "approved",
    "rejected",
)


class Denial(Base):
    """A single ingested claim-denial letter, scoped to one company profile."""

    __tablename__ = "denials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Which company-profile pipeline configuration this record belongs to
    # (e.g. "comprehensive_eyecare_partners", later "reliable_medical").
    source_company: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    payer: Mapped[str] = mapped_column(String(255), nullable=False)
    claim_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*DENIAL_STATUSES, name="denial_status"),
        nullable=False,
        default="new",
        server_default="new",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        Index("ix_denials_status", "status"),
        Index("ix_denials_source_company", "source_company"),
        Index("ix_denials_source_company_status", "source_company", "status"),
    )


class Extraction(Base):
    """Structured fields extracted from a denial letter by the LLM extraction step."""

    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    denial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("denials.id", ondelete="CASCADE"), nullable=False
    )
    extracted_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_model_output: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (Index("ix_extractions_denial_id", "denial_id"),)


CLASSIFICATION_CATEGORIES = (
    "coding_error",
    "missing_information",
    "medical_necessity",
    "timely_filing",
    "eligibility",
    "duplicate_claim",
)


class Classification(Base):
    """Denial-reason category assigned to a denial by the LLM classification step."""

    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    denial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("denials.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(
        Enum(*CLASSIFICATION_CATEGORIES, name="classification_category"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (Index("ix_classifications_denial_id", "denial_id"),)


class Appeal(Base):
    """A drafted appeal letter for a denial, and its human-review lifecycle."""

    __tablename__ = "appeals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    denial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("denials.id", ondelete="CASCADE"), nullable=False
    )
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("draft", "approved", "rejected", "sent", name="appeal_status"),
        nullable=False,
        default="draft",
        server_default="draft",
    )
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (Index("ix_appeals_denial_id", "denial_id"),)


AUDIT_EVENT_TYPES = ("correction", "appeal_review")


class AuditEvent(Base):
    """Immutable, append-only compliance audit trail: every human action worth a
    permanent record on a denial -- a correction to an AI-produced field, or an
    appeal approve/reject/sent review decision. Rows are INSERT-only; nothing
    here is ever updated or deleted in normal operation, which is the whole
    point (see `Appeal.status`/`reviewer`/`reviewed_at`, which DO get
    overwritten in place to show "current state" -- this table is the
    permanent history those mutable fields don't preserve).

    One wider table with two event shapes, distinguished by `event_type`,
    rather than two separate tables, because the shapes turned out to share
    the same four columns cleanly:
      - event_type="correction": field_corrected/old_value/new_value describe
        what changed (e.g. "classification.category"), corrected_by is who
        made the fix. Unchanged from the original `corrections` table.
      - event_type="appeal_review": field_corrected is always "appeal.status",
        old_value/new_value are the previous/new appeal status
        (draft -> approved, etc.), corrected_by is the reviewer, and
        `appeal_id` records which Appeal row (there can be more than one per
        denial after a reprocess) the decision was about.
    Table name stayed `corrections` (rather than renaming to `audit_events`)
    to keep the migration that extended it a pure ADD COLUMN, not a rename --
    see alembic/versions for the migration that added event_type/appeal_id.
    """

    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    denial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("denials.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(
        Enum(*AUDIT_EVENT_TYPES, name="audit_event_type"),
        nullable=False,
        default="correction",
        server_default="correction",
    )
    # Only set for event_type="appeal_review" -- which Appeal row (a denial
    # can have more than one, after a reprocess) this review decision was
    # about. SET NULL on appeal delete so the audit row survives even if the
    # appeal it referenced is ever removed.
    appeal_id: Mapped[int | None] = mapped_column(
        ForeignKey("appeals.id", ondelete="SET NULL"), nullable=True
    )
    # e.g. "classification.category", "appeal.draft_text", "extraction.claim_amount"
    # for corrections; always "appeal.status" for appeal_review events.
    field_corrected: Mapped[str] = mapped_column(String(128), nullable=False)
    old_value: Mapped[str] = mapped_column(Text, nullable=False)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_by: Mapped[str] = mapped_column(String(128), nullable=False)
    corrected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_corrections_denial_id", "denial_id"),
        Index("ix_corrections_appeal_id", "appeal_id"),
        Index("ix_corrections_event_type", "event_type"),
    )


# Backwards-compatible alias: the table/model used to be correction-only.
Correction = AuditEvent


class EvalRun(Base):
    """Result of a later phase's eval harness run, tied to the code version that produced it."""

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    accuracy_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)


class TokenUsage(Base):
    """Per-call LLM token/cost accounting for every pipeline stage, for cost tracking."""

    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Nullable: some calls (e.g. eval-harness runs) aren't tied to a specific denial.
    denial_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("denials.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[str] = mapped_column(
        Enum("extraction", "classification", "appeal_drafting", name="token_usage_stage"),
        nullable=False,
    )
    input_tokens: Mapped[int] = mapped_column(nullable=False)
    output_tokens: Mapped[int] = mapped_column(nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (Index("ix_token_usage_denial_id", "denial_id"),)
