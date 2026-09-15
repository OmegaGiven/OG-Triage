"""
Main loop: fetch recent Gmail, classify each new message, and for real
job-inquiry emails -- extract details, tailor a resume if a JD is present,
draft a grounded reply, and create a Gmail DRAFT with the resume attached.
Nothing is ever auto-sent. Run this on a schedule (cron) or by hand.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import classify
import db
import draft_reply
import extract
import gmail_client
import resume_builder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("og-triage-gmail")

CLASSIFY_CONFIDENCE_THRESHOLD = 0.65
DEFAULT_QUERY = "newer_than:3d -in:sent -in:drafts"


def process_message(service, conn, message_id: str, dry_run: bool) -> None:
    if db.already_processed(conn, message_id):
        return

    email = gmail_client.get_normalized_message(service, message_id)
    log.info(f"Checking: {email.subject!r} from {email.from_address}")

    try:
        cls = classify.classify_email(email.from_address, email.subject, email.body_text)
    except Exception as e:
        log.error(f"classify failed for {message_id}: {e}")
        db.log_result(conn, message_id, email.thread_id, email.from_address, email.subject,
                       status="error", error=f"classify: {e}")
        return

    if not cls["is_job_inquiry"] or cls["confidence"] < CLASSIFY_CONFIDENCE_THRESHOLD:
        log.info(f"  -> not a job inquiry (confidence {cls['confidence']:.2f}), skipping")
        db.log_result(conn, message_id, email.thread_id, email.from_address, email.subject,
                       status="skipped_not_inquiry", is_job_inquiry=cls["is_job_inquiry"],
                       classify_confidence=cls["confidence"], classify_reasoning=cls["reasoning"])
        return

    log.info(f"  -> job inquiry (confidence {cls['confidence']:.2f}): {cls['reasoning']}")

    combined_text = extract.gather_jd_text(email)
    try:
        extracted = extract.extract_inquiry(email, combined_text)
    except Exception as e:
        log.error(f"extract failed for {message_id}: {e}")
        db.log_result(conn, message_id, email.thread_id, email.from_address, email.subject,
                       status="error", is_job_inquiry=True, classify_confidence=cls["confidence"],
                       classify_reasoning=cls["reasoning"], error=f"extract: {e}")
        return

    log.info(f"  -> extracted: {extracted['company_name']} / {extracted['role_title']}, "
              f"asks={extracted['asks']}, has_jd={extracted['has_job_description']}")

    resume_docx_path = resume_pdf_path = None
    resume_selection = None
    resume_is_tailored = False

    if extracted["has_job_description"] and "resume" in extracted.get("asks", []):
        try:
            result = resume_builder.build_tailored_resume(
                jd_text=combined_text,
                company=extracted["company_name"],
                role_title=extracted["role_title"],
            )
            resume_docx_path = result["docx_path"]
            resume_pdf_path = result["pdf_path"]
            resume_selection = result["selection"]
            resume_is_tailored = True
            log.info(f"  -> tailored resume built: {resume_pdf_path}")
        except Exception as e:
            log.error(f"resume tailoring failed, falling back to base resume: {e}")

    resume_attached_path = None
    if resume_pdf_path:
        resume_attached_path = resume_pdf_path
    elif "resume" in extracted.get("asks", []):
        # Fallback: attach the existing base resume rather than nothing.
        fallback = Path.home() / "resumes" / "Nathan_Johnson_Resume.pdf"
        if fallback.exists():
            resume_attached_path = str(fallback)

    try:
        body = draft_reply.draft_reply_body(
            original_subject=email.subject,
            original_body=email.body_text,
            extracted=extracted,
            resume_attached=resume_attached_path is not None,
            resume_is_tailored=resume_is_tailored,
        )
    except Exception as e:
        log.error(f"draft_reply failed for {message_id}: {e}")
        db.log_result(conn, message_id, email.thread_id, email.from_address, email.subject,
                       status="error", is_job_inquiry=True, classify_confidence=cls["confidence"],
                       classify_reasoning=cls["reasoning"], extracted=extracted,
                       resume_docx_path=resume_docx_path, resume_pdf_path=resume_pdf_path,
                       resume_selection=resume_selection, error=f"draft_reply: {e}")
        return

    if dry_run:
        log.info("  -> DRY RUN, not creating a Gmail draft. Reply body would be:\n" + body)
        db.log_result(conn, message_id, email.thread_id, email.from_address, email.subject,
                       status="dry_run", is_job_inquiry=True, classify_confidence=cls["confidence"],
                       classify_reasoning=cls["reasoning"], extracted=extracted,
                       resume_docx_path=resume_docx_path, resume_pdf_path=resume_pdf_path,
                       resume_selection=resume_selection)
        return

    attachments = []
    if resume_attached_path:
        data = Path(resume_attached_path).read_bytes()
        attachments.append((Path(resume_attached_path).name, "application/pdf", data))

    try:
        draft_id = gmail_client.create_draft_reply(
            service,
            thread_id=email.thread_id,
            to_address=email.from_address,
            subject=email.subject,
            body_text=body,
            in_reply_to_message_id=email.message_id,
            attachments=attachments,
        )
    except Exception as e:
        log.error(f"create_draft_reply failed for {message_id}: {e}")
        db.log_result(conn, message_id, email.thread_id, email.from_address, email.subject,
                       status="error", is_job_inquiry=True, classify_confidence=cls["confidence"],
                       classify_reasoning=cls["reasoning"], extracted=extracted,
                       resume_docx_path=resume_docx_path, resume_pdf_path=resume_pdf_path,
                       resume_selection=resume_selection, error=f"create_draft: {e}")
        return

    log.info(f"  -> Gmail draft created: {draft_id}")
    db.log_result(conn, message_id, email.thread_id, email.from_address, email.subject,
                   status="draft_created", is_job_inquiry=True, classify_confidence=cls["confidence"],
                   classify_reasoning=cls["reasoning"], extracted=extracted,
                   resume_docx_path=resume_docx_path, resume_pdf_path=resume_pdf_path,
                   resume_selection=resume_selection, draft_id=draft_id)


def main():
    parser = argparse.ArgumentParser(description="OG-Triage Gmail job-inquiry assistant")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Gmail search query for candidate messages")
    parser.add_argument("--max", type=int, default=25, help="Max messages to check per run")
    parser.add_argument("--dry-run", action="store_true", help="Don't create Gmail drafts, just log what would happen")
    args = parser.parse_args()

    service = gmail_client.get_service()
    conn = db.get_conn()

    message_ids = gmail_client.list_recent_message_ids(service, args.query, max_results=args.max)
    log.info(f"Found {len(message_ids)} candidate messages for query: {args.query!r}")

    for mid in message_ids:
        process_message(service, conn, mid, dry_run=args.dry_run)

    conn.close()


if __name__ == "__main__":
    main()
