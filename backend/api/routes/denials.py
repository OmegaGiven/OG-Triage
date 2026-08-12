"""Denial listing/detail, on-demand pipeline processing, appeal review, and
the corrections audit-trail endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import (
    AppealOut,
    AppealStatusUpdateRequest,
    CorrectionCreateRequest,
    CorrectionOut,
    DenialDetail,
    DenialListItem,
    DenialListResponse,
    ProcessResponse,
)
from db.models import Appeal, Classification, Correction, Denial, Extraction

router = APIRouter(prefix="/api/denials", tags=["denials"])

APPEAL_STATUSES = ("approved", "rejected", "sent")


def _get_denial_or_404(db: Session, denial_id: uuid.UUID) -> Denial:
    denial = db.get(Denial, denial_id)
    if denial is None:
        raise HTTPException(status_code=404, detail=f"no denial with id={denial_id}")
    return denial


@router.get("", response_model=DenialListResponse)
def list_denials(
    source_company: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> DenialListResponse:
    query = db.query(Denial)
    if source_company:
        query = query.filter(Denial.source_company == source_company)
    if status:
        query = query.filter(Denial.status == status)

    total = query.count()

    has_classification = exists().where(Classification.denial_id == Denial.id)
    has_appeal = exists().where(Appeal.denial_id == Denial.id)

    rows = (
        query.with_entities(Denial, has_classification.label("has_classification"), has_appeal.label("has_appeal"))
        .order_by(Denial.received_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        DenialListItem(
            id=denial.id,
            source_company=denial.source_company,
            status=denial.status,
            payer=denial.payer,
            claim_ref=denial.claim_ref,
            received_at=denial.received_at,
            has_classification=bool(has_classification_flag),
            has_appeal=bool(has_appeal_flag),
        )
        for denial, has_classification_flag, has_appeal_flag in rows
    ]

    return DenialListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{denial_id}", response_model=DenialDetail)
def get_denial_detail(denial_id: uuid.UUID, db: Session = Depends(get_db)) -> DenialDetail:
    denial = _get_denial_or_404(db, denial_id)

    extraction = (
        db.query(Extraction)
        .filter(Extraction.denial_id == denial_id)
        .order_by(Extraction.created_at.desc())
        .first()
    )
    classification = (
        db.query(Classification)
        .filter(Classification.denial_id == denial_id)
        .order_by(Classification.created_at.desc())
        .first()
    )
    appeal = (
        db.query(Appeal)
        .filter(Appeal.denial_id == denial_id)
        .order_by(Appeal.created_at.desc())
        .first()
    )
    corrections = (
        db.query(Correction)
        .filter(Correction.denial_id == denial_id)
        .order_by(Correction.corrected_at.desc())
        .all()
    )

    return DenialDetail(
        id=denial.id,
        source_company=denial.source_company,
        status=denial.status,
        payer=denial.payer,
        claim_ref=denial.claim_ref,
        received_at=denial.received_at,
        created_at=denial.created_at,
        raw_text=denial.raw_text,
        extraction=extraction,
        classification=classification,
        appeal=appeal,
        corrections=[CorrectionOut.model_validate(c) for c in corrections],
    )


@router.post("/{denial_id}/process", response_model=ProcessResponse)
def process_denial(denial_id: uuid.UUID, db: Session = Depends(get_db)) -> ProcessResponse:
    """Runs the real extract -> classify -> draft_appeal pipeline for this one
    denial, on-demand. Makes real Anthropic API calls -- real cost."""
    # Local import: pipeline.run opens its own SessionLocal() and shouldn't
    # be pulled in at module-import time for routes that never call it.
    from pipeline.run import process_denial_by_id

    _get_denial_or_404(db, denial_id)
    try:
        status = process_denial_by_id(denial_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProcessResponse(denial_id=denial_id, status=status)


@router.post("/{denial_id}/appeal/status", response_model=AppealOut)
def update_appeal_status(
    denial_id: uuid.UUID, body: AppealStatusUpdateRequest, db: Session = Depends(get_db)
) -> AppealOut:
    _get_denial_or_404(db, denial_id)

    if body.status not in APPEAL_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {APPEAL_STATUSES}, got {body.status!r}",
        )

    appeal = (
        db.query(Appeal)
        .filter(Appeal.denial_id == denial_id)
        .order_by(Appeal.created_at.desc())
        .first()
    )
    if appeal is None:
        raise HTTPException(status_code=404, detail=f"no appeal drafted yet for denial_id={denial_id}")

    appeal.status = body.status
    appeal.reviewer = body.reviewer
    appeal.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(appeal)
    return appeal


@router.post("/{denial_id}/corrections", response_model=CorrectionOut, status_code=201)
def create_correction(
    denial_id: uuid.UUID, body: CorrectionCreateRequest, db: Session = Depends(get_db)
) -> CorrectionOut:
    """Logs a human correction to an AI-produced field -- the compliance
    audit-trail feature. Does not mutate the corrected field itself
    (extraction/classification/appeal rows are left as the AI produced
    them); this only appends an audit row."""
    _get_denial_or_404(db, denial_id)

    correction = Correction(
        denial_id=denial_id,
        field_corrected=body.field_corrected,
        old_value=body.old_value,
        new_value=body.new_value,
        corrected_by=body.corrected_by,
        notes=body.notes,
    )
    db.add(correction)
    db.commit()
    db.refresh(correction)
    return correction
