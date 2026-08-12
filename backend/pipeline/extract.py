"""
Stage 1: structured field extraction from a denial letter's raw_text.

Uses a forced tool-use call (tool_choice pinned to the single extraction
tool) instead of asking Claude for freeform JSON and hoping it parses --
forcing the tool call means the SDK hands back an already-parsed Python dict
(ToolUseBlock.input) instead of a string we'd have to json.loads() and hope
is well-formed. The "verification reflex" this buys: a malformed response
becomes a missing/empty tool_use block or an absent required field, both of
which we check for explicitly and retry on, rather than a JSON parse
exception (or worse, silently-wrong data) buried downstream.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from db.models import Denial, Extraction

from .common import (
    ANTHROPIC_MODEL,
    PROMPT_VERSION,
    PipelineStageError,
    call_with_retry,
    get_client,
    record_token_usage,
)

EXTRACTION_TOOL_NAME = "record_extraction"

EXTRACTION_TOOL = {
    "name": EXTRACTION_TOOL_NAME,
    "description": (
        "Record the structured fields extracted from an insurance claim "
        "denial letter. Only include values that are explicitly stated in "
        "the letter -- never infer, guess, or fabricate a value that isn't "
        "present in the text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_ref": {
                "type": "string",
                "description": "Patient reference/ID as stated in the letter (e.g. 'PT-107231').",
            },
            "claim_ref": {
                "type": "string",
                "description": "The payer's claim number for this denial (e.g. 'CLM-6123212').",
            },
            "date_of_service": {
                "type": "string",
                "description": "Date of service, normalized to YYYY-MM-DD.",
            },
            "billed_amount": {
                "type": "number",
                "description": "Billed amount in US dollars as a plain number, no currency symbol or commas.",
            },
            "cpt_code": {
                "type": "string",
                "description": "CPT procedure code, digits only (e.g. '67028').",
            },
            "cpt_description": {
                "type": "string",
                "description": "The procedure description accompanying the CPT code, if the letter states one.",
            },
            "diagnosis_code": {
                "type": "string",
                "description": "ICD-10 diagnosis code exactly as written (e.g. 'H35.3212').",
            },
            "diagnosis_description": {
                "type": "string",
                "description": "The diagnosis description accompanying the ICD-10 code, if the letter states one.",
            },
            "physician_name": {
                "type": "string",
                "description": "Treating physician's name including credentials (e.g. 'Dr. Amara Delgado, MD').",
            },
            "physician_npi": {
                "type": "string",
                "description": "Treating physician's NPI number, digits only.",
            },
            "payer_name": {
                "type": "string",
                "description": "Name of the insurance payer that issued the denial letter.",
            },
            "carc_code": {
                "type": "string",
                "description": "Claim Adjustment Reason Code number, digits only (e.g. '29').",
            },
            "carc_description": {
                "type": "string",
                "description": "The CARC's description text as stated in the letter, if given.",
            },
            "rarc_code": {
                "type": "string",
                "description": (
                    "Remittance Advice Remark Code, if one is cited in the letter "
                    "(e.g. 'N211'). Omit this property entirely if no RARC is present."
                ),
            },
            "rarc_description": {
                "type": "string",
                "description": "The RARC's description text, if given. Omit if there is no RARC.",
            },
            "prior_auth_number": {
                "type": "string",
                "description": (
                    "Prior authorization number, ONLY if the letter references one. "
                    "Omit this property entirely if no prior authorization is mentioned."
                ),
            },
        },
        "required": [
            "patient_ref",
            "claim_ref",
            "date_of_service",
            "billed_amount",
            "cpt_code",
            "diagnosis_code",
            "physician_name",
            "physician_npi",
            "payer_name",
            "carc_code",
        ],
        "additionalProperties": False,
    },
}

REQUIRED_FIELDS = tuple(EXTRACTION_TOOL["input_schema"]["required"])

SYSTEM_PROMPT = (
    "You are a meticulous medical billing/coding assistant for Comprehensive "
    "EyeCare Partners, an eye-care multi-specialty organization (MSO). You "
    "extract structured data from insurance claim denial letters with zero "
    "tolerance for fabrication: every field you report must be traceable to "
    "an exact phrase in the letter. If a field genuinely is not present in "
    "the letter, leave it out of your tool call rather than guessing at it."
)


@dataclass
class StageResult:
    success: bool
    data: dict | None
    raw_output: str
    error: str | None
    input_tokens: int
    output_tokens: int


def _run_once(client, denial: Denial) -> StageResult:
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": EXTRACTION_TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Extract the structured fields from this denial letter using "
                    f"the {EXTRACTION_TOOL_NAME} tool.\n\n---\n\n{denial.raw_text}"
                ),
            }
        ],
    )

    # Full content-block dump, not just text -- for a forced tool call there
    # is typically no text block at all, so this is the genuine "raw model
    # response" for the audit trail.
    raw_output = json.dumps([block.model_dump() for block in response.content], default=str)
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None or tool_block.name != EXTRACTION_TOOL_NAME:
        raise PipelineStageError(
            f"expected a '{EXTRACTION_TOOL_NAME}' tool_use block, got "
            f"stop_reason={response.stop_reason!r} "
            f"content_types={[b.type for b in response.content]!r}"
        )

    data = tool_block.input
    missing = [f for f in REQUIRED_FIELDS if f not in data or data[f] in (None, "")]
    if missing:
        raise PipelineStageError(f"tool call missing required field(s): {missing}")

    return StageResult(
        success=True,
        data=data,
        raw_output=raw_output,
        error=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def extract_denial(db, denial: Denial) -> StageResult:
    """
    Run extraction for one denial: call Claude (retrying once on a transient
    or malformed-response failure), write the extractions audit row --
    success or failure -- and a token_usage row on success, and return the
    result. Does not commit; the caller (pipeline.run) owns the transaction
    boundary so this can be committed together with the denial's status
    update.
    """
    client = get_client()
    try:
        result = call_with_retry(
            lambda: _run_once(client, denial), description=f"extract[{denial.id}]"
        )
    except PipelineStageError as exc:
        # Audit trail for the failure itself: extracted_fields and
        # raw_model_output are both non-nullable, so we record the error in
        # place of data we never got rather than leaving no row at all.
        db.add(
            Extraction(
                denial_id=denial.id,
                extracted_fields={"error": True, "message": str(exc)},
                model_version=ANTHROPIC_MODEL,
                prompt_version=PROMPT_VERSION,
                raw_model_output=str(exc),
            )
        )
        return StageResult(
            success=False,
            data=None,
            raw_output="",
            error=str(exc),
            input_tokens=0,
            output_tokens=0,
        )

    record_token_usage(
        db,
        denial_id=denial.id,
        stage="extraction",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    db.add(
        Extraction(
            denial_id=denial.id,
            extracted_fields=result.data,
            model_version=ANTHROPIC_MODEL,
            prompt_version=PROMPT_VERSION,
            raw_model_output=result.raw_output,
        )
    )
    return result
