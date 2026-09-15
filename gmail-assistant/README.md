# OG-Triage: Gmail Job-Inquiry Assistant

Personal instance of the OG-Triage harness, wired to your Gmail. Scans
recent mail, identifies recruiter/hiring emails asking about you, and
prepares a **Gmail draft** (never sent automatically) with:

- A reply grounded strictly in `facts.json` — it will never state anything
  about you that isn't in that file, and will never reveal anything listed
  under `facts.never_reveal`.
- A resume attached — tailored to the specific job description if one was
  found in the email/attachments (built from `content_library.json`'s real
  bullets/projects, never invented facts), otherwise your base resume.

Everything lands as a draft in the original Gmail thread for you to read,
edit, and send yourself.

## One-time setup

### 1. Google Cloud OAuth client (you have to do this part — it's your Google account)

1. Go to https://console.cloud.google.com/ , create a new project (or reuse
   one), name doesn't matter (e.g. "og-triage-personal").
2. **APIs & Services → Library** — enable the **Gmail API**.
3. **APIs & Services → OAuth consent screen** — choose **External**, fill
   in the minimal required fields (app name, your email). Under **Scopes**,
   add `gmail.readonly` and `gmail.compose`. Under **Test users**, add your
   own Gmail address (this app stays in "Testing" mode indefinitely, which
   is fine — it's just for you, not published).
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   — Application type: **Desktop app**. Download the JSON.
5. Save the downloaded file as `credentials.json` in this directory
   (`gmail-assistant/credentials.json`). It's gitignored — never gets
   committed.

### 2. Python environment

```bash
cd ~/projects/OG-Triage/gmail-assistant
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Environment variable

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

### 4. First run — browser consent

```bash
.venv/bin/python run.py --dry-run
```

This opens your browser once for you to approve access (readonly + compose
scopes only — the code never requests `gmail.send` at all, so it's
structurally incapable of sending mail). After approving, a `token.json`
is cached here and future runs won't prompt again unless it expires.

`--dry-run` logs everything it would do (classification, extraction,
tailored resume, draft reply text) without actually creating a Gmail
draft or writing to state.sqlite as "processed" — safe to run repeatedly
while you're checking it's behaving the way you want.

### 5. Review your facts

Open `facts.json` and edit it to match what you actually want revealed —
phone, LinkedIn, compensation framing, what to never disclose. This file
is the entire source of truth for what the assistant says about you.
`content_library.json` holds the real resume bullets/projects it tailors
from — add to it if you want new projects/bullets available for selection.

## Running for real

```bash
.venv/bin/python run.py
```

Checks Gmail messages from the last 3 days (`newer_than:3d`) by default,
classifies each, and for genuine job inquiries creates a Gmail draft. Run
it again later and already-processed messages are skipped automatically
(tracked in `state.sqlite`).

Useful flags:

```bash
python run.py --query "newer_than:7d"     # widen the search window
python run.py --max 50                    # check more messages per run
python run.py --dry-run                   # log only, no drafts created
```

### Running on a schedule

Once you're happy with the output quality from a few `--dry-run` and real
passes, wire it to cron (e.g. every 30 minutes):

```
*/30 * * * * cd /home/omegagiven/projects/OG-Triage/gmail-assistant && .venv/bin/python run.py >> run.log 2>&1
```

## What it will never do

- Never sends email — Gmail `send` scope isn't even requested. Drafts only.
- Never states a fact about you outside `facts.json`.
- Never reveals anything in `facts.never_reveal` (exact address, references,
  salary history, other active interview processes) regardless of what's asked.
- Never invents an employer, project, date, or achievement on a tailored
  resume — the tailoring model only selects/reorders from `content_library.json`.

## Files

| File | Purpose |
|---|---|
| `facts.json` | What you're willing to reveal — the only source of truth in reply drafts. |
| `content_library.json` | Real resume bullets/projects — the only source of truth in tailored resumes. |
| `gmail_client.py` | OAuth, message fetch/normalize, draft creation. |
| `classify.py` | Stage 1: is this a job inquiry? |
| `extract.py` | Stage 2: company/role/asks/JD text (incl. PDF/DOCX attachment parsing). |
| `resume_builder.py` | Tailors a resume docx→PDF per JD from `content_library.json`. |
| `draft_reply.py` | Composes the reply body, grounded in `facts.json`. |
| `db.py` | SQLite dedup + audit log (`state.sqlite`). |
| `run.py` | Orchestrates all of the above. |
