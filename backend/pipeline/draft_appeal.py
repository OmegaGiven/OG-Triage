"""
Stage 3: draft an appeal letter for a denial, given its extraction and
classification.

Unlike extract/classify, this stage's output is prose, not structured data,
so it does not use tool-use -- there is no schema to force. What matters
here is that the letter cites the *specific* facts of this claim (claim
ref, patient ref, date of service, CPT code, physician + NPI, prior auth
number when relevant) rather than reading as a generic template; the system
prompt and per-category guidance below are built around that, and the
appeal is rejected (PipelineStageError, retried once) if it comes back
looking templated -- missing the claim ref, or too short to plausibly
address the denial.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.models import Appeal, Denial

from .common import ANTHROPIC_MODEL, PipelineStageError, call_with_retry, get_client, record_token_usage

# Category-specific instructions for what the appeal actually needs to argue
# -- a generic "please reconsider" letter fails for every category, but what
# makes a *substantive* appeal differs by category.
APPEAL_GUIDANCE = {
    "coding_error": (
        "Identify the specific coding issue the denial cites, state the "
        "correct code/modifier/combination and why it applies to the "
        "documented procedure and diagnosis, and request the claim be "
        "reprocessed under the corrected coding."
    ),
    "missing_information": (
        "State plainly that the requested information/documentation is "
        "being provided with this appeal (referencing what it is, based on "
        "the extracted fields), and request reprocessing once it is on "
        "file."
    ),
    "medical_necessity": (
        "Provide a substantive clinical justification for medical "
        "necessity: cite the diagnosis, why the procedure is the "
        "appropriate standard of care for that diagnosis, and any relevant "
        "clinical context implied by the letter. Request reconsideration on "
        "medical-necessity grounds."
    ),
    "timely_filing": (
        "Address the timely-filing gap directly. If the letter or extracted "
        "fields suggest a qualifying exception (e.g. evidence the original "
        "claim was submitted on time, or a payer-caused delay), assert it "
        "explicitly. Otherwise request a good-cause exception and state "
        "what supporting evidence of timely original submission is being "
        "provided."
    ),
    "eligibility": (
        "Assert that the patient was eligible/covered on the date of "
        "service (or explain why the service is a covered benefit under "
        "the plan), referencing the date of service and patient reference. "
        "If a prior authorization number was extracted, cite it as evidence "
        "the service was pre-approved. Request reconsideration."
    ),
    "duplicate_claim": (
        "Explain concretely why this claim is not a duplicate -- e.g. a "
        "distinct date of service, procedure, or laterality from any prior "
        "claim -- and request the claim be reprocessed as a unique, "
        "non-duplicate submission."
    ),
}

SYSTEM_PROMPT_TEMPLATE = (
    "You are drafting a formal written appeal on behalf of Comprehensive "
    "EyeCare Partners, an eye-care MSO, appealing an insurance claim denial. "
    "You will be given the denial letter, the structured fields already "
    "extracted from it, and the classified denial category with the "
    "reasoning behind it.\n\n"
    "Write a complete, ready-to-send professional appeal letter. Hard "
    "requirements:\n"
    "- Reference the specific facts of THIS claim by name: the claim "
    "number, patient reference, date of service, CPT code (with its "
    "description), and the treating physician's name and NPI. Cite the "
    "prior authorization number too if one was extracted and it is "
    "relevant to this denial category.\n"
    "- Directly address the stated denial reason (CARC/RARC code and "
    "category) with a substantive counter-argument or corrective action -- "
    "not a generic request for reconsideration.\n"
    "- Category-specific guidance for this denial "
    "({category}): {guidance}\n"
    "- Do not fabricate any fact not present in the extracted fields or the "
    "letter (e.g. do not invent clinical notes, dates, or documentation "
    "that wasn't given to you) -- where the letter is asking you to assert "
    "something you don't have evidence for, phrase it as what is being "
    "submitted/attached rather than inventing the content.\n"
    "- Standard business-letter format: date, addressee (the payer's "
    "appeals department), RE line with claim number and patient reference, "
    "body, and a signature block for Comprehensive EyeCare Partners' "
    "billing/appeals department.\n"
    "- Output ONLY the letter text -- no preamble, no commentary, no "
    "markdown formatting."
)


@dataclass
class StageResult:
    success: bool
    draft_text: str | None
    error: str | None
    input_tokens: int
    output_tokens: int


def _build_user_message(denial: Denial, extracted_fields: dict, classification: dict) -> str:
    import json

    return (
        f"Extracted fields:\n{json.dumps(extracted_fields, indent=2)}\n\n"
        f"Classification: category={classification['category']!r}, "
        f"confidence={classification['confidence']:.2f}\n"
        f"Classification reasoning: {classification['reasoning']}\n\n"
        f"Original denial letter:\n---\n{denial.raw_text}\n---\n\n"
        "Draft the appeal letter now."
    )


def _run_once(client, denial: Denial, extracted_fields: dict, classification: dict) -> StageResult:
    category = classification["category"]
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        category=category, guidance=APPEAL_GUIDANCE[category]
    )

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=[
            {"role": "user", "content": _build_user_message(denial, extracted_fields, classification)}
        ],
    )

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    text_blocks = [b.text for b in response.content if b.type == "text"]
    draft_text = "\n".join(text_blocks).strip()

    if not draft_text or len(draft_text) < 200:
        raise PipelineStageError(f"appeal draft implausibly short ({len(draft_text)} chars)")

    # Cheap grounding check: the drafted letter should cite the claim number
    # we know is correct (from the denial row itself, not the extraction --
    # this also catches an extraction error corrupting the claim ref before
    # it reaches the letter) and the extracted physician NPI. A generic,
    # ungrounded letter fails this and gets retried once.
    missing_anchors = []
    if denial.claim_ref not in draft_text:
        missing_anchors.append("claim_ref")
    npi = extracted_fields.get("physician_npi")
    if npi and npi not in draft_text:
        missing_anchors.append("physician_npi")
    if missing_anchors:
        raise PipelineStageError(
            f"appeal draft is missing required grounding detail(s): {missing_anchors}"
        )

    return StageResult(
        success=True,
        draft_text=draft_text,
        error=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def draft_appeal(db, denial: Denial, extracted_fields: dict, classification: dict) -> StageResult:
    """
    Draft an appeal letter for one denial, given its extraction and
    classification. Retries once on a transient error or a draft that fails
    the grounding check. Writes an appeals row on success and a token_usage
    row. Does not commit.
    """
    client = get_client()
    try:
        result = call_with_retry(
            lambda: _run_once(client, denial, extracted_fields, classification),
            description=f"draft_appeal[{denial.id}]",
        )
    except PipelineStageError as exc:
        return StageResult(
            success=False, draft_text=None, error=str(exc), input_tokens=0, output_tokens=0
        )

    record_token_usage(
        db,
        denial_id=denial.id,
        stage="appeal_drafting",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    db.add(Appeal(denial_id=denial.id, draft_text=result.draft_text, status="draft"))
    return result
