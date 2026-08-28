"""Stage 0 — pull. Gmail OAuth (desktop flow) + incremental metadata fetch.

Only subject/from/snippet/id are fetched here. Bodies are fetched later, only for
the few update candidates that reach Stage 2 (fetch_body).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Read-only: we never modify the mailbox.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


@dataclass
class EmailMeta:
    id: str
    thread_id: str
    subject: str
    sender: str
    snippet: str
    internal_date: int  # ms since epoch

    @property
    def text(self) -> str:
        """The signal Stage 1 embeds: subject + snippet."""
        return f"{self.subject}\n{self.snippet}".strip()


def _service(credentials_path: Path, token_path: Path):
    """Build an authenticated Gmail API client, running the browser flow once."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"{credentials_path} not found — download the OAuth desktop client "
                    "JSON from Google Cloud (see README) before the first run."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def pull(
    credentials_path: Path,
    token_path: Path,
    since: datetime,
    max_results: int = 200,
) -> list[EmailMeta]:
    """Fetch message metadata newer than `since` (a timezone-aware datetime)."""
    svc = _service(credentials_path, token_path)
    after_epoch = int(since.timestamp())
    query = f"after:{after_epoch}"

    ids: list[str] = []
    req = svc.users().messages().list(userId="me", q=query, maxResults=min(max_results, 500))
    while req is not None and len(ids) < max_results:
        resp = req.execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        req = svc.users().messages().list_next(req, resp)
    ids = ids[:max_results]

    out: list[EmailMeta] = []
    for mid in ids:
        msg = (
            svc.users()
            .messages()
            .get(
                userId="me",
                id=mid,
                format="metadata",
                metadataHeaders=["Subject", "From"],
            )
            .execute()
        )
        payload = msg.get("payload", {})
        out.append(
            EmailMeta(
                id=msg["id"],
                thread_id=msg.get("threadId", msg["id"]),
                subject=_header(payload, "Subject"),
                sender=_header(payload, "From"),
                snippet=msg.get("snippet", ""),
                internal_date=int(msg.get("internalDate", 0)),
            )
        )
    return out


def fetch_body(credentials_path: Path, token_path: Path, msg_id: str, max_chars: int = 4000) -> str:
    """Fetch and decode the plain-text body of a single message (Stage 2 only)."""
    svc = _service(credentials_path, token_path)
    msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()

    def walk(part: dict) -> str:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        text = ""
        for sub in part.get("parts", []):
            text += walk(sub)
        # Fall back to HTML if no plain text was found anywhere.
        if not text and mime == "text/html" and data:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return _strip_html(html)
        return text

    body = walk(msg.get("payload", {})).strip()
    return body[:max_chars]


def _strip_html(html: str) -> str:
    import re

    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()
