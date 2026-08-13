"""Pydantic request/response models for the API.

Kept separate from db/models.py (the SQLAlchemy ORM layer) on purpose --
these describe the wire contract with the frontend, not the DB schema, and
the two are allowed to diverge (e.g. list vs. detail views, computed
booleans like `has_classification`).
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Denials
# ---------------------------------------------------------------------------


class DenialListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_company: str
    status: str
    payer: str
    claim_ref: str
    received_at: datetime.datetime
    has_classification: bool
    has_appeal: bool


class DenialListResponse(BaseModel):
    items: list[DenialListItem]
    total: int
    page: int
    page_size: int


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    extracted_fields: dict
    model_version: str
    prompt_version: str
    raw_model_output: str
    created_at: datetime.datetime


class ClassificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    confidence: float
    model_version: str
    created_at: datetime.datetime


class AppealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    draft_text: str
    status: str
    reviewer: str | None
    reviewed_at: datetime.datetime | None
    created_at: datetime.datetime


class CorrectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    field_corrected: str
    old_value: str
    new_value: str
    corrected_by: str
    corrected_at: datetime.datetime
    notes: str | None


class DenialDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_company: str
    status: str
    payer: str
    claim_ref: str
    received_at: datetime.datetime
    created_at: datetime.datetime
    raw_text: str
    extraction: ExtractionOut | None
    classification: ClassificationOut | None
    appeal: AppealOut | None
    corrections: list[CorrectionOut]


class ProcessResponse(BaseModel):
    denial_id: uuid.UUID
    status: str


class AppealStatusUpdateRequest(BaseModel):
    status: str = Field(description="One of: approved, rejected, sent")
    reviewer: str = Field(min_length=1)


class CorrectionCreateRequest(BaseModel):
    field_corrected: str = Field(min_length=1)
    old_value: str
    new_value: str
    corrected_by: str = Field(min_length=1)
    notes: str | None = None


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


class EvalRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_at: datetime.datetime
    accuracy_score: float | None
    git_commit: str | None
    details: dict | None


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


class ProfileOut(BaseModel):
    key: str
    display_name: str


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class UsageByStage(BaseModel):
    stage: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    call_count: int


class UsageByDay(BaseModel):
    day: datetime.date
    total_tokens: int
    estimated_cost_usd: float
    call_count: int


class UsageResponse(BaseModel):
    source_company: str | None
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_estimated_cost_usd: float
    total_calls: int
    by_stage: list[UsageByStage]
    by_day: list[UsageByDay]


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


class ConfidenceBucket(BaseModel):
    label: str = Field(description="Human-readable bucket range, e.g. '0.70-0.85'")
    min_confidence: float
    max_confidence: float
    count: int


class ConfidenceDistributionResponse(BaseModel):
    source_company: str | None
    total_classified: int = Field(description="Denials with a classification counted (one per denial, most recent)")
    buckets: list[ConfidenceBucket]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    database: str
