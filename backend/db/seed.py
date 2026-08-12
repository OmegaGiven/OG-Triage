#!/usr/bin/env python3
"""
Loads backend/data/denials_synthetic.json into the `denials` table.

Idempotent: each synthetic record's `claim_ref` is treated as a stable
identifier for seeding purposes -- rows are only inserted if no existing
`denials` row has that claim_ref for the same source_company. Safe to
re-run; a second run inserts zero additional rows.

Usage:
    python backend/db/seed.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.models import Denial  # noqa: E402
from db.session import SessionLocal  # noqa: E402

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "denials_synthetic.json"


def load_denials(session):
    records = json.loads(DATA_PATH.read_text())

    existing_claim_refs = {
        row[0]
        for row in session.query(Denial.claim_ref).filter(
            Denial.source_company == records[0]["source_company"] if records else ""
        )
    }

    inserted = 0
    skipped = 0
    for rec in records:
        if rec["claim_ref"] in existing_claim_refs:
            skipped += 1
            continue

        denial = Denial(
            source_company=rec["source_company"],
            raw_text=rec["raw_text"],
            payer=rec["payer"],
            claim_ref=rec["claim_ref"],
            received_at=rec["received_at"],
            status=rec.get("status", "new"),
        )
        session.add(denial)
        inserted += 1

    session.commit()
    return inserted, skipped


def main():
    if not DATA_PATH.exists():
        raise SystemExit(
            f"Synthetic dataset not found at {DATA_PATH}. "
            "Run backend/data/generate_synthetic_data.py first."
        )

    session = SessionLocal()
    try:
        inserted, skipped = load_denials(session)
    finally:
        session.close()

    print(f"Inserted {inserted} denial(s), skipped {skipped} already-present record(s).")


if __name__ == "__main__":
    main()
