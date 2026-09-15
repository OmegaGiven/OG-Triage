"""Stage 3: compose the reply body. Grounded entirely in facts.json --
the model is handed the facts file verbatim (including never_reveal) and
instructed never to state anything about Nathan that isn't in it."""

from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic

HERE = Path(__file__).parent
FACTS = json.loads((HERE / "facts.json").read_text())

ANTHROPIC_MODEL = "claude-sonnet-5"

SYSTEM = (
    "You draft email replies on Nathan Johnson's behalf to recruiters/hiring "
    "contacts, for his review before sending -- never sent automatically. "
    "You are given a JSON 'facts' object that is the ONLY source of truth about "
    "him. Rules:\n"
    "1. Never state a fact about Nathan that isn't in the facts object.\n"
    "2. Never reveal anything listed under facts.never_reveal.\n"
    "3. Only mention the compensation target if compensation.disclose_number_unprompted "
    "is true, OR the sender directly asked for a number/range.\n"
    "4. Match facts.reply_tone.\n"
    "5. If a resume is attached, reference it naturally ('I've attached my resume...').\n"
    "6. Sign off with the name/email/phone from facts.identity.\n"
    "7. Keep it to a realistic email length -- a few short paragraphs, not an essay."
)


def draft_reply_body(
    original_subject: str,
    original_body: str,
    extracted: dict,
    resume_attached: bool,
    resume_is_tailored: bool,
) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)

    user = (
        f"Original email subject: {original_subject}\n\n"
        f"Original email body:\n{original_body[:6000]}\n\n"
        f"Extracted context: {json.dumps(extracted)}\n\n"
        f"Resume attached: {resume_attached} (tailored to this specific role: {resume_is_tailored})\n\n"
        f"Facts about Nathan (only source of truth):\n{json.dumps(FACTS, indent=2)}\n\n"
        "Write the reply body now. Plain text, no markdown formatting, no subject line."
    )
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=800,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in resp.content if block.type == "text").strip()
