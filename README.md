# Gauge AI Automations — Claims Denial Triage + Appeal Drafting

**Status: Phase 1 only.** This repo will eventually hold a pipeline that ingests
insurance claim-denial letters, extracts structured fields, classifies the
denial reason, and drafts an appeal letter, with a Postgres-backed audit
trail and a React/TypeScript review UI. **None of that is built yet.** What
exists today is the foundation the rest of the build sits on:

- The Postgres schema (`denials`, `extractions`, `classifications`,
  `appeals`, `corrections`, `eval_runs`, `token_usage`), defined as
  SQLAlchemy models with an Alembic migration.
- A 48-record synthetic dataset of realistic ophthalmology claim-denial
  letters for a fictional company, "Comprehensive EyeCare Partners," plus a
  24-record labeled subset for regression-testing a future eval harness.
- A seed script to load the synthetic dataset into Postgres.

There is no FastAPI application, no LLM pipeline, and no frontend yet. The
`backend/requirements.txt` includes `fastapi`, `anthropic`, and `pydantic`
because those are pinned dependencies for the next phase, not because
anything currently uses them.

## Repo layout

```
app/
  backend/
    db/
      models.py          # SQLAlchemy models (the schema)
      session.py          # engine/session, reads DATABASE_URL
      seed.py              # loads backend/data/denials_synthetic.json into Postgres
    data/
      denials_synthetic.json      # 48 synthetic denial letters
      eval_labeled.json           # 24-record labeled subset (ground truth + required appeal elements)
      generate_synthetic_data.py  # re-runnable, seeded generator that produces the two files above
    alembic/                      # migrations (one initial migration, matches models.py)
    requirements.txt
    .env.example
  frontend/                       # placeholder, real scaffold is a later phase
  docker-compose.yml              # Postgres only
  README.md
```

## Setup

### 1. Start Postgres

```bash
docker-compose up -d
```

This brings up a single `postgres:16-alpine` container on **host port
5544** (not the Postgres default 5432 — picked to avoid colliding with any
Postgres already running on the dev machine), with a named volume
(`gauge_ai_pgdata`) for persistence and a healthcheck. Database:
`gauge_ai_claims`, user: `gauge`. See `docker-compose.yml` for credentials
(dev-only, not for anything resembling production use).

### 2. Python environment

```bash
cd backend
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # adjust DATABASE_URL if you changed docker-compose.yml
```

`requirements.txt` uses `>=` floors rather than hard pins — `psycopg2-binary`
does not currently ship prebuilt wheels for very new CPython releases, so
this repo depends on `psycopg[binary]` (psycopg 3) instead, and pins are
left loose enough for `pip` to resolve versions with available wheels for
whatever interpreter you're on.

### 3. Run the migration

```bash
cd backend
alembic upgrade head
```

`alembic/env.py` reads `DATABASE_URL` from the environment (via
`db/session.py`, which loads `.env`), so make sure step 2's `.env` is in
place, or export `DATABASE_URL` directly:

```bash
DATABASE_URL="postgresql+psycopg://gauge:gauge_dev_password@localhost:5544/gauge_ai_claims" alembic upgrade head
```

### 4. Seed the synthetic dataset

```bash
python backend/db/seed.py
```

Loads all 48 denials from `backend/data/denials_synthetic.json` into the
`denials` table (`source_company = "comprehensive_eyecare_partners"`,
`status = "new"`). Idempotent — re-running skips rows whose `claim_ref`
already exists rather than duplicating them.

### Regenerating the synthetic dataset

```bash
python backend/data/generate_synthetic_data.py
```

Deterministic (fixed random seed) — re-running produces byte-identical
output. Regenerate after editing the payer/procedure/diagnosis pools or
sentence templates in that script. This only rewrites the two JSON files;
it does not touch the database — run `seed.py` again afterward if you want
the new data loaded (note the current idempotency key is `claim_ref`, so if
you change the generator's random seed you'll get a new set of claim refs
and end up with both old and new rows in the database unless you truncate
`denials` first).

## Synthetic data notes

- 48 denial letters, evenly split (8 each) across the six classification
  categories: `coding_error`, `missing_information`, `medical_necessity`,
  `timely_filing`, `eligibility`, `duplicate_claim`.
- Every letter uses real, currently-active CARC (Claim Adjustment Reason
  Code) and RARC (Remittance Advice Remark Code) values, verified against
  the X12 code lists at x12.org/codes — see the comments above
  `CATEGORIES` in `generate_synthetic_data.py` for the specific codes used
  and their source.
- Two writing styles per letter (terse EOB-style vs. longer narrative
  manual-review letter), randomly assigned, to avoid the dataset looking
  like one brittle format.
- All patient references, claim numbers, NPIs, payer names, and dollar
  amounts are synthetic. No real names, no real PHI.
- `eval_labeled.json` is a 24-record subset (evenly spread across
  categories) with ground-truth `category`/CARC/RARC and a
  `required_appeal_elements` list per record, built from that record's
  actual generated values (claim number, CPT code, physician NPI, etc.) —
  not generic boilerplate.

## What's next (not built yet)

FastAPI routes, the extraction/classification/appeal-drafting LLM pipeline,
the eval harness that scores against `eval_labeled.json`, and the React
frontend.
