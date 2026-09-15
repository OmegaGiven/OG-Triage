"""
Gmail connector: OAuth2 (installed-app flow, one-time browser consent, then
cached refresh token), message fetch + normalization, and draft creation
with attachments. Never sends anything -- create_draft_reply only ever
creates a Gmail draft, review/send is entirely manual.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

HERE = Path(__file__).parent
CREDENTIALS_PATH = HERE / "credentials.json"  # downloaded from Google Cloud Console, see README
TOKEN_PATH = HERE / "token.json"  # created on first run after browser consent

# Broad read scope (per your choice: full-inbox auto-detection, not a
# label-gated subset) + compose scope for draft creation. Never requests
# gmail.send -- structurally cannot send mail even if the code had a bug.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

MAX_ATTACHMENT_BYTES = 5_000_000  # skip parsing anything larger (safety cap)


@dataclass
class Attachment:
    filename: str
    mime_type: str
    data: bytes


@dataclass
class NormalizedEmail:
    message_id: str
    thread_id: str
    from_address: str
    to_address: str
    subject: str
    body_text: str
    body_html: str | None
    attachments: list[Attachment] = field(default_factory=list)
    internal_date_ms: int = 0


def get_service():
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                raise RuntimeError(
                    f"{CREDENTIALS_PATH} not found. See README.md for how to create a "
                    "Google Cloud OAuth client and download this file."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _find_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _walk_parts(part: dict, out_text: list[str], out_html: list[str], out_attachments: list[dict]):
    mime_type = part.get("mimeType", "")
    body = part.get("body", {})
    filename = part.get("filename", "")

    if filename:
        out_attachments.append(part)
    elif mime_type == "text/plain" and "data" in body:
        out_text.append(_b64d(body["data"]))
    elif mime_type == "text/html" and "data" in body:
        out_html.append(_b64d(body["data"]))

    for sub in part.get("parts", []) or []:
        _walk_parts(sub, out_text, out_html, out_attachments)


def _b64d(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def list_recent_message_ids(service, query: str, max_results: int = 25) -> list[str]:
    """query is a standard Gmail search query, e.g. 'newer_than:3d -label:og-triage-processed'."""
    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    return [m["id"] for m in resp.get("messages", [])]


def get_normalized_message(service, message_id: str) -> NormalizedEmail:
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = msg["payload"]
    headers = payload.get("headers", [])

    text_parts: list[str] = []
    html_parts: list[str] = []
    attachment_parts: list[dict] = []
    _walk_parts(payload, text_parts, html_parts, attachment_parts)

    attachments: list[Attachment] = []
    for part in attachment_parts:
        body = part.get("body", {})
        size = body.get("size", 0)
        if size > MAX_ATTACHMENT_BYTES:
            continue
        attachment_id = body.get("attachmentId")
        if not attachment_id:
            continue
        att = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
        data = base64.urlsafe_b64decode(att["data"].encode())
        mime_type = part.get("mimeType", mimetypes.guess_type(part.get("filename", ""))[0] or "application/octet-stream")
        attachments.append(Attachment(filename=part.get("filename", "attachment"), mime_type=mime_type, data=data))

    return NormalizedEmail(
        message_id=msg["id"],
        thread_id=msg["threadId"],
        from_address=_find_header(headers, "From"),
        to_address=_find_header(headers, "To"),
        subject=_find_header(headers, "Subject"),
        body_text="\n".join(text_parts).strip(),
        body_html="\n".join(html_parts).strip() or None,
        attachments=attachments,
        internal_date_ms=int(msg.get("internalDate", 0)),
    )


def create_draft_reply(
    service,
    thread_id: str,
    to_address: str,
    subject: str,
    body_text: str,
    in_reply_to_message_id: str,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> str:
    """Creates a Gmail DRAFT (never sends). attachments is a list of
    (filename, mime_type, bytes). Returns the created draft's id."""
    msg = MIMEMultipart()
    msg["To"] = to_address
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg.attach(MIMEText(body_text, "plain"))

    for filename, mime_type, data in attachments or []:
        maintype, subtype = (mime_type.split("/", 1) if "/" in mime_type else ("application", "octet-stream"))
        part = MIMEApplication(data, _subtype=subtype)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body = {"message": {"raw": raw, "threadId": thread_id}}
    draft = service.users().drafts().create(userId="me", body=body).execute()
    return draft["id"]
