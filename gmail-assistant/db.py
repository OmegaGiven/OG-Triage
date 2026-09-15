"""Lightweight SQLite audit log + dedup store. A personal, single-user tool
doesn't need the full multi-tenant Postgres schema the main OG-Triage
harness uses -- same audit-trail principle (every decision logged,
immutable), smaller footprint."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "state.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    from_address TEXT,
    subject TEXT,
    is_job_inquiry INTEGER,
    classify_confidence REAL,
    classify_reasoning TEXT,
    extracted_json TEXT,
    resume_docx_path TEXT,
    resume_pdf_path TEXT,
    resume_selection_json TEXT,
    draft_id TEXT,
    status TEXT NOT NULL,
    error TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def already_processed(conn: sqlite3.Connection, message_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)).fetchone()
    return row is not None


def log_result(
    conn: sqlite3.Connection,
    message_id: str,
    thread_id: str,
    from_address: str,
    subject: str,
    status: str,
    is_job_inquiry: bool | None = None,
    classify_confidence: float | None = None,
    classify_reasoning: str | None = None,
    extracted: dict | None = None,
    resume_docx_path: str | None = None,
    resume_pdf_path: str | None = None,
    resume_selection: dict | None = None,
    draft_id: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO processed_messages
        (message_id, thread_id, processed_at, from_address, subject, is_job_inquiry,
         classify_confidence, classify_reasoning, extracted_json, resume_docx_path,
         resume_pdf_path, resume_selection_json, draft_id, status, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            thread_id,
            datetime.now().isoformat(),
            from_address,
            subject,
            int(is_job_inquiry) if is_job_inquiry is not None else None,
            classify_confidence,
            classify_reasoning,
            json.dumps(extracted) if extracted else None,
            resume_docx_path,
            resume_pdf_path,
            json.dumps(resume_selection) if resume_selection else None,
            draft_id,
            status,
            error,
        ),
    )
    conn.commit()
