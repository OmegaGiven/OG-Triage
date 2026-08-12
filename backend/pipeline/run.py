"""
Orchestrates a denial through extract -> classify -> draft_appeal, advancing
db/models.py's Denial.status through its lifecycle as each stage completes:

    new -> processing -> classified -> appeal_drafted
                      (or) -> needs_review (low classification confidence,
                                             or any stage failing outright)

Callable two ways:
  - As a library function, for a later on-demand API endpoint:
        from pipeline.run import process_denial_by_id
  - As a batch script, from backend/:
        python -m pipeline.run --all
    which processes every denial currently in status="new".
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.models import Denial  # noqa: E402
from db.session import SessionLocal  # noqa: E402

from pipeline.classify import classify_denial  # noqa: E402
from pipeline.draft_appeal import draft_appeal  # noqa: E402
from pipeline.extract import extract_denial  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pipeline.run")

# Confidence threshold below which a classified denial is routed to
# needs_review instead of continuing on to appeal drafting.
#
# 0.7 is a deliberately middle-of-the-road cut for a 6-way, well-specified
# categorization task (each category maps to fairly distinctive CARC codes
# per classify.py's CATEGORY_GUIDE): below it, the model is expressing real
# uncertainty about the root cause, and drafting an appeal that argues the
# wrong point wastes more staff time reviewing than it saves -- better to
# route straight to a human. Above it, the categorization task is
# constrained enough that false "high confidence" calls should be rare, and
# every drafted appeal still passes through appeals.status="draft" for
# human review before it's ever sent, so 0.7 doesn't need to be
# conservative to the point of paranoia. This is a starting point, not a
# tuned value -- Phase 3's eval harness against eval_labeled.json is what
# should actually calibrate it (e.g. by plotting confidence vs. correctness
# and picking the threshold that trades off review load against error rate).
CONFIDENCE_THRESHOLD = 0.7


def process_denial(db, denial: Denial) -> str:
    """Run one denial through the full pipeline. Commits after every status
    transition so a crash mid-pipeline leaves the denial in whatever state
    it last successfully reached, not in an inconsistent partial state."""
    logger.info("[%s] processing (claim_ref=%s)", denial.id, denial.claim_ref)
    denial.status = "processing"
    db.commit()

    extraction = extract_denial(db, denial)
    if not extraction.success:
        logger.error("[%s] extraction failed: %s", denial.id, extraction.error)
        denial.status = "needs_review"
        db.commit()
        return denial.status
    db.commit()  # persist the successful extraction row + token_usage row

    classification = classify_denial(db, denial, extraction.data)
    if not classification.success:
        logger.error("[%s] classification failed: %s", denial.id, classification.error)
        denial.status = "needs_review"
        db.commit()
        return denial.status

    denial.status = "classified"
    db.commit()  # persist the classification row + token_usage row + status

    confidence = classification.data["confidence"]
    category = classification.data["category"]
    if confidence < CONFIDENCE_THRESHOLD:
        logger.warning(
            "[%s] confidence %.2f below threshold %.2f (category=%s) -- routing to needs_review",
            denial.id, confidence, CONFIDENCE_THRESHOLD, category,
        )
        denial.status = "needs_review"
        db.commit()
        return denial.status

    appeal = draft_appeal(db, denial, extraction.data, classification.data)
    if not appeal.success:
        logger.error("[%s] appeal drafting failed: %s", denial.id, appeal.error)
        denial.status = "needs_review"
        db.commit()
        return denial.status

    denial.status = "appeal_drafted"
    db.commit()
    logger.info(
        "[%s] done -> appeal_drafted (category=%s, confidence=%.2f)",
        denial.id, category, confidence,
    )
    return denial.status


def process_denial_by_id(denial_id: uuid.UUID | str) -> str:
    """Single-denial entry point -- e.g. for a future on-demand API endpoint
    that reprocesses one denial. Opens and closes its own session."""
    db = SessionLocal()
    try:
        denial = db.get(Denial, denial_id)
        if denial is None:
            raise ValueError(f"no denial with id={denial_id}")
        return process_denial(db, denial)
    finally:
        db.close()


def process_all_new(limit: int | None = None) -> dict[str, int]:
    """Batch entry point: process every denial with status='new'. Returns a
    dict of final-status -> count."""
    db = SessionLocal()
    try:
        query = db.query(Denial).filter(Denial.status == "new").order_by(Denial.claim_ref)
        if limit:
            query = query.limit(limit)
        denials = query.all()
        logger.info("found %d denial(s) with status='new'", len(denials))

        counts: dict[str, int] = {}
        for denial in denials:
            try:
                status = process_denial(db, denial)
            except Exception:
                # Anything that escapes process_denial is a bug, not an
                # expected pipeline failure (those are handled per-stage and
                # already route to needs_review). Still: don't let one
                # denial's unhandled exception kill the whole batch or leave
                # the session in a broken transaction for the next denial.
                logger.exception("[%s] unhandled error -- marking needs_review", denial.id)
                db.rollback()
                denial = db.get(Denial, denial.id)
                denial.status = "needs_review"
                db.commit()
                status = "needs_review"
            counts[status] = counts.get(status, 0) + 1
        return counts
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Run the denial triage + appeal-drafting pipeline.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Process every denial with status='new'.")
    group.add_argument("--denial-id", type=str, help="Process a single denial by id.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap the number of denials processed with --all."
    )
    args = parser.parse_args()

    if args.denial_id:
        status = process_denial_by_id(args.denial_id)
        print(f"denial {args.denial_id}: {status}")
    else:
        counts = process_all_new(limit=args.limit)
        print("\nBatch run complete:")
        for status, n in sorted(counts.items()):
            print(f"  {status}: {n}")


if __name__ == "__main__":
    main()
