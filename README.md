# Gauge AI Automations — Claims Denial Triage + Appeal Drafting

**Status: Phase 1-4 done.** This repo holds a pipeline that ingests insurance
claim-denial letters, extracts structured fields, classifies the denial
reason, and drafts an appeal letter, with a Postgres-backed audit trail and a
deterministic eval/regression harness. The pipeline is profile-driven and
currently drives two portfolio companies' claim types (eye-care and DME) off
one shared codebase. A React/TypeScript review UI is not built yet.

- **Phase 1** — the Postgres schema (`denials`, `extractions`,
  `classifications`, `appeals`, `corrections`, `eval_runs`, `token_usage`),
  defined as SQLAlchemy models with an Alembic migration; a 48-record
  synthetic dataset of realistic ophthalmology claim-denial letters for a
  fictional company, "Comprehensive EyeCare Partners," plus a 24-record
  labeled subset for regression-testing the eval harness; a seed script to
  load the synthetic dataset into Postgres.
- **Phase 2** — `backend/pipeline/` (`extract.py`, `classify.py`,
  `draft_appeal.py`, `run.py`, `common.py`): a real Anthropic-API pipeline
  that takes a denial from `status="new"` through extraction, classification,
  and (confidence permitting) appeal drafting, with per-call token/cost
  accounting (`token_usage`) and a confidence-gated `needs_review` routing
  path. Runnable as `python -m pipeline.run --all` from `backend/`.
- **Phase 3** — `backend/eval/` (`score.py`, `run_eval.py`): a deterministic
  eval harness that scores the pipeline's persisted results against
  `eval_labeled.json`'s ground truth and writes a regression-checked
  `eval_runs` row. See "Phase 3 — Evaluation" below.
- **Phase 4** — `backend/profiles/`: refactored the Phase 2 pipeline to be
  driven by a `CompanyProfile` (extraction schema, prompts, appeal guidance)
  resolved from `denials.source_company`, instead of hardcoded
  Comprehensive-EyeCare-Partners text, and added a second company profile,
  "Reliable Medical" (a fictional national DME/CRT provider), with its own
  synthetic dataset. Proves the architecture generalizes to a genuinely
  different claim type on the same pipeline code. See "Phase 4 —
  Multi-Company Profiles" below.

The `backend/requirements.txt` includes `fastapi` and `pydantic` because
those are pinned dependencies for a future API/frontend phase, not because
anything currently uses them.

## Repo layout

```
app/
  backend/
    db/
      models.py          # SQLAlchemy models (the schema)
      session.py          # engine/session, reads DATABASE_URL
      seed.py              # loads a denials JSON file into Postgres (any company)
    data/
      denials_synthetic.json                    # 48 synthetic CEP denial letters
      eval_labeled.json                         # 24-record labeled subset (CEP, ground truth + required appeal elements)
      generate_synthetic_data.py                # re-runnable, seeded generator that produces the two files above
      denials_reliable_medical.json              # 14 synthetic Reliable Medical (DME) denial letters
      generate_synthetic_data_reliable_medical.py # re-runnable, seeded generator for the DME dataset
    profiles/                     # Phase 4: CompanyProfile abstraction (see below)
      base.py
      comprehensive_eyecare_partners.py
      reliable_medical_dme.py
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
already exists rather than duplicating them. Pass a different path to seed
another company's dataset, e.g.
`python backend/db/seed.py backend/data/denials_reliable_medical.json`.

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

## Phase 3 — Evaluation

`backend/eval/` is a deterministic regression harness for the Phase 2
pipeline. It makes **no LLM calls** — it only reads what the pipeline already
wrote to Postgres and compares it to `backend/data/eval_labeled.json`'s
hand-labeled ground truth, so it's cheap and safe to re-run on every change.

### Running it

```bash
cd backend
python -m eval.run_eval
```

This scores the 24 labeled denials against their existing DB rows
(`extractions`, `classifications`, `appeals` — it does **not** re-run the
pipeline; if a labeled denial's rows are genuinely missing, that's reported
as a data issue, not silently re-processed), prints the score breakdown,
writes a new `eval_runs` row, and compares against the most recent prior
`eval_runs` row to flag a regression. `python -m eval.score` runs just the
scoring step and dumps the full JSON breakdown to stdout, without touching
the database.

### The three scoring dimensions

1. **Classification accuracy** — binary, per denial: does
   `classifications.category` exactly match `eval_labeled.json`'s
   `ground_truth_category`?
2. **Extraction accuracy** — binary, per denial: does
   `extractions.extracted_fields['carc_code']` exactly match
   `ground_truth_carc_code`? RARC match, and claim_ref/patient_ref sanity
   checks against values regexed directly out of the denial's own
   `raw_text` (independent of anything the pipeline produced), are also
   computed and reported, but only the CARC match feeds the weighted score
   — see `eval/score.py` for why RARC is reported separately (the synthetic
   letters never state a RARC code in their `raw_text`, so
   `ground_truth_rarc_code` isn't something a letter-grounded extractor
   could ever legitimately recover; scoring it into the same number as CARC
   would make a correctly-behaving extractor look broken).
3. **Appeal completeness** — for each denial's `required_appeal_elements`,
   the harness extracts the literal facts embedded in that requirement's own
   text (claim/patient/prior-auth refs, CPT/ICD-10/CARC codes, dates, NPIs)
   and checks each is a substring of the drafted appeal letter. A
   requirement with no extractable literal (e.g. "must address the timely
   filing gap directly") is reported separately as "unverifiable" rather
   than scored pass/fail — the harness deliberately does not use an LLM
   judge to assess free-form argument quality; that would defeat the point
   of a cheap, deterministic regression gate. Only denials that reached
   `appeal_drafted` are scored on this dimension; denials routed to
   `needs_review` (low classification confidence) have no appeal to check
   and are reported as a separate count, not folded in as an implicit zero.

The overall score is `0.4 * classification_accuracy + 0.3 * extraction_accuracy
+ 0.3 * appeal_completeness` — classification is weighted highest because a
wrong category drives the `needs_review` routing decision and the
category-specific appeal-drafting guidance, so it tends to cascade into
downstream errors even when extraction and drafting both work correctly. See
the comment above `WEIGHT_CLASSIFICATION` in `eval/score.py` for the full
reasoning.

### The `eval_runs` row

Each run inserts one row: `accuracy_score` (the overall weighted score),
`details` (the full JSON breakdown — per-dimension aggregates and a
per-denial list with predicted vs. ground truth, missing/unverifiable appeal
elements, etc.), `git_commit` (`git rev-parse HEAD` at run time, so a score
is always traceable to the exact code that produced it), and `run_at`.

### Regression threshold

`run_eval.py` compares the new run's `accuracy_score` against the most
recent prior `eval_runs` row (skipped gracefully if there isn't one — e.g.
the very first run) and flags a regression if the score dropped by more than
**5 percentage points**. With only 24 labeled examples, a single denial
flipping from correct to incorrect on classification alone already moves the
weighted score by ~1.7pp, so the threshold needs to sit above that
single-flip noise floor while still catching a real regression (e.g. a
prompt change that breaks a whole category, typically a double-digit-pp
move). See the comment above `REGRESSION_THRESHOLD_PP` in `eval/run_eval.py`
for the full reasoning — this is a starting point tuned by judgment, not a
statistically derived value, and the right long-term fix if it proves noisy
is a larger labeled set, not just retuning the number.

## Phase 4 — Multi-Company Profiles

The Phase 2 pipeline (`extract.py`/`classify.py`/`draft_appeal.py`) was
originally built with Comprehensive EyeCare Partners' extraction schema and
prompt text hardcoded directly into those three files. Phase 4 pulls all of
that company-specific configuration out into `backend/profiles/`, so the
same pipeline code drives multiple portfolio companies, and adds a second
profile — "Reliable Medical," a fictional national complex rehab technology
(CRT) / durable medical equipment (DME) provider — to prove it's a real
abstraction and not just a plan for one.

### How the profile system works

`backend/profiles/base.py` defines `CompanyProfile`, a frozen dataclass
holding everything that varies per company:

- `key` / `display_name` — `key` must exactly match the `source_company`
  value on that company's `denials` rows; it's the lookup key.
- `extraction_tool` — the full Anthropic tool schema (name, description,
  `input_schema`) forced via `tool_choice` in stage 1. Different companies
  have genuinely different structured fields (e.g. `cpt_code` vs.
  `hcpcs_code`, and DME-only fields like `equipment_type` and
  `lmn_reference_number` for a Letter of Medical Necessity reference — a
  real DME-specific document requirement eye-care claims have no equivalent
  of). `extraction_required_fields` is derived from the schema's
  `required` array, not duplicated by hand.
- `extraction_system_prompt` — company/domain framing for stage 1.
- `classification_system_prompt_intro` + `category_guide` — company framing
  and a CARC-code guide for stage 2, combined by
  `classification_system_prompt()`.
- `appeal_guidance` (per category) + `appeal_system_prompt_template` +
  `appeal_grounding_fields` — stage 3's per-category argument guidance, the
  system-prompt template (filled in via `appeal_system_prompt(category)`),
  and which extracted fields the drafted letter is checked against for
  grounding.

What deliberately does **not** vary per profile: the six
`CLASSIFICATION_CATEGORIES` in `db/models.py`
(`coding_error`/`missing_information`/`medical_necessity`/`timely_filing`/
`eligibility`/`duplicate_claim`) and the classification tool's JSON schema
(`classify.py`'s `CLASSIFICATION_TOOL`) are shared across every profile —
they're genuine root-cause buckets for any healthcare claim denial, not
eye-care- or DME-specific, so a new profile should adjust the CARC-code
guide and appeal guidance text, not invent new categories.

`backend/profiles/__init__.py` registers each company's `PROFILE` in a
`PROFILES` dict and exposes `get_profile(source_company)`. `pipeline/run.py`
resolves the profile once per denial (`get_profile(denial.source_company)`)
and passes it into `extract_denial()`, `classify_denial()`, and
`draft_appeal()`, which use it to build their tool schema/prompts instead of
referencing hardcoded module-level constants.

### Adding a third company profile

1. Create `backend/profiles/<new_company_key>.py`, using
   `comprehensive_eyecare_partners.py` or `reliable_medical_dme.py` as a
   template: define `EXTRACTION_TOOL`, `EXTRACTION_SYSTEM_PROMPT`,
   `CLASSIFICATION_SYSTEM_PROMPT_INTRO` + `CATEGORY_GUIDE`, and
   `APPEAL_GUIDANCE` + `APPEAL_SYSTEM_PROMPT_TEMPLATE`, then build a
   module-level `PROFILE = CompanyProfile(...)`.
2. Register it in `PROFILES` in `backend/profiles/__init__.py`.
3. Add a synthetic denial dataset under `backend/data/` (a generator script
   following `generate_synthetic_data_reliable_medical.py`'s pattern is the
   easiest way to get varied, non-mail-merged letters), with
   `source_company` matching the new profile's `key` exactly.
4. Seed it: `python backend/db/seed.py backend/data/denials_<new_company>.json`.

Nothing in `pipeline/*.py` needs to change — extraction/classification/
appeal-drafting all resolve their behavior from the `CompanyProfile` at
runtime.

### Running the pipeline against a specific profile

```bash
cd backend
python -m pipeline.run --all --source-company reliable_medical
python -m pipeline.run --all --source-company comprehensive_eyecare_partners
python -m pipeline.run --all   # every source_company's status="new" denials
python -m pipeline.run --denial-id <uuid>   # single denial; profile is
                                             # resolved automatically from
                                             # that denial's source_company
```

### Reliable Medical (DME) dataset

`backend/data/denials_reliable_medical.json` — 14 synthetic denial letters
(`source_company = "reliable_medical"`), generated by
`generate_synthetic_data_reliable_medical.py` (deterministic, fixed seed,
same varied-prose approach as the CEP generator: terse EOB-style vs.
narrative manual-review letters, randomly assigned). Category distribution:
`coding_error` (3), `missing_information` (3), `medical_necessity` (2),
`timely_filing` (2), `eligibility` (2), `duplicate_claim` (2). Equipment
types are varied across the set: manual and power wheelchairs, CPAP/BiPAP
devices, a semi-electric hospital bed, a custom ankle-foot orthosis, and a
microprocessor-knee lower-limb prosthesis. CARC codes are the same
verified, X12-maintained codes used in the CEP dataset (CARCs are generic
across specialties, not eye-care- or DME-specific) — re-verified against
x12.org/codes for this phase. HCPCS Level II equipment codes (K0823, E1130,
E0601, E0470, E0260, L1960, L5856) were verified against
AAPC/CMS-coding-reference sources.

### Verification

Ran the refactored pipeline for real against the Anthropic API for both
profiles: all 14 Reliable Medical denials (12 reached `appeal_drafted`, 2
correctly routed to `needs_review` on low classification confidence — the
existing 0.7 threshold behavior, unchanged), and a 3-denial CEP spot-check
(re-run through the refactored code) confirmed extraction/classification/
appeal output is unchanged in character from before the refactor — eye-care
CPT/diagnosis language, no DME terms. The DME outputs were checked for the
reverse: `extracted_fields` show `hcpcs_code`/`equipment_type`/
`lmn_reference_number` (never `cpt_code`), and drafted appeals cite HCPCS
codes, equipment type, and LMN/CMN documentation rather than eye-care
language — confirming the profile switch actually changes model behavior
rather than the DME data just flowing through CEP-flavored prompts.

## What's next (not built yet)

FastAPI routes to expose the pipeline and eval results, and the React
frontend.
