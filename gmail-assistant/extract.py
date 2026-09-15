"""Stage 2: pull structured fields out of a confirmed job-inquiry email --
company, role, what's actually being asked, and any job description text
(inline in the body or in an attachment)."""

from __future__ import annotations

import io
import os

import anthropic
from docx import Document
from pypdf import PdfReader

from gmail_client import Attachment, NormalizedEmail

ANTHROPIC_MODEL = "claude-sonnet-5"

EXTRACT_TOOL = {
    "name": "extract_inquiry",
    "description": "Extract structured fields from a recruiter/hiring email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "Company hiring, or the recruiting agency if the end client isn't named."},
            "role_title": {"type": "string"},
            "asks": {
                "type": "array",
                "description": "What the sender is actually asking for.",
                "items": {
                    "type": "string",
                    "enum": [
                        "resume",
                        "availability",
                        "salary_expectation",
                        "work_authorization",
                        "general_interest",
                        "schedule_call",
                        "references",
                        "other",
                    ],
                },
            },
            "has_job_description": {
                "type": "boolean",
                "description": "True if a real job description (responsibilities/requirements) is present in the body or an attachment, not just a one-line role title.",
            },
            "sender_name": {"type": "string"},
        },
        "required": ["company_name", "role_title", "asks", "has_job_description"],
    },
}

SYSTEM = (
    "Extract structured fields from a recruiter/hiring email for Nathan Johnson's "
    "personal job-search assistant. If no company/role is stated clearly, use your "
    "best inference from context and note uncertainty is fine -- don't fabricate a "
    "specific company/role that isn't implied by the text."
)


def _extract_attachment_text(att: Attachment) -> str:
    try:
        if att.mime_type == "application/pdf" or att.filename.lower().endswith(".pdf"):
            reader = PdfReader(io.BytesIO(att.data))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        if att.filename.lower().endswith(".docx"):
            doc = Document(io.BytesIO(att.data))
            return "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        return ""
    return ""


def gather_jd_text(email: NormalizedEmail) -> str:
    """Body text plus any parseable PDF/DOCX attachment text, concatenated.
    This is the raw candidate text -- extract_inquiry() below decides whether
    it actually constitutes a real job description."""
    parts = [email.body_text]
    for att in email.attachments:
        text = _extract_attachment_text(att)
        if text.strip():
            parts.append(f"\n--- attachment: {att.filename} ---\n{text}")
    return "\n".join(parts).strip()


def extract_inquiry(email: NormalizedEmail, combined_text: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)

    user = (
        f"From: {email.from_address}\nSubject: {email.subject}\n\n"
        f"Full text (body + any attachments):\n{combined_text[:12000]}"
    )
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=500,
        system=SYSTEM,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_inquiry"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("model did not return a tool_use block")
