"""Stage 1: is this email actually a recruiter/interview email asking for
info about Nathan/his resume? Cheap gate before spending a full extraction
call on everything in the inbox."""

from __future__ import annotations

import os

import anthropic

ANTHROPIC_MODEL = "claude-sonnet-5"

CLASSIFY_TOOL = {
    "name": "classify_email",
    "description": "Decide whether this email is a recruiter/hiring-related message requesting information about the recipient (resume, availability, qualifications, etc.).",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_job_inquiry": {
                "type": "boolean",
                "description": "True if this is a recruiter/hiring manager/ATS email asking about the recipient's background, resume, availability, or fit for a role.",
            },
            "confidence": {
                "type": "number",
                "description": "0.0 to 1.0 confidence in the is_job_inquiry call.",
            },
            "reasoning": {"type": "string", "description": "One sentence why."},
        },
        "required": ["is_job_inquiry", "confidence", "reasoning"],
    },
}

SYSTEM = (
    "You screen inbound email for Nathan Johnson's personal job-search assistant. "
    "Classify whether an email is a genuine recruiter/hiring-related inquiry asking "
    "about him specifically (his resume, background, availability, fit for a role) -- "
    "not a job-board digest/newsletter, not an automated 'application received' "
    "confirmation with no ask, not spam, not an unrelated personal or work email. "
    "A recruiter sharing a job description and asking his thoughts, or directly "
    "asking a question about his background/availability, counts as True."
)


def classify_email(from_address: str, subject: str, body_text: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)

    user = f"From: {from_address}\nSubject: {subject}\n\nBody:\n{body_text[:6000]}"
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=300,
        system=SYSTEM,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_email"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("model did not return a tool_use block")
