"""Gmail API send helpers (MASTER_SPECIFICATION §7.1).

Uses OAuth 2.0 with scope ``gmail.send`` only. Never uses SMTP.
"""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any

from app.config import get_settings

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SCOPES = [GMAIL_SEND_SCOPE]


def parse_recipient_emails(raw: str | None) -> list[str]:
    """Split CEO_EMAIL on commas/semicolons/whitespace; keep unique addresses."""
    if not raw or not str(raw).strip():
        return []
    found: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\s]+", str(raw).strip()):
        address = part.strip().strip("<>").strip()
        if not address or not _EMAIL_RE.match(address):
            continue
        key = address.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(address)
    return found


def gmail_authenticated_email(creds: Any) -> str | None:
    """Best-effort sender address for the From: header (helps inbox placement)."""
    settings = get_settings()
    configured = (getattr(settings, "gmail_from_email", None) or "").strip()
    if configured and _EMAIL_RE.match(configured):
        return configured
    try:
        from googleapiclient.discovery import build

        profile = (
            build("gmail", "v1", credentials=creds, cache_discovery=False)
            .users()
            .getProfile(userId="me")
            .execute()
        )
        address = (profile.get("emailAddress") or "").strip()
        if address and _EMAIL_RE.match(address):
            return address
    except Exception:
        return None
    return None


def build_internal_rfc822(
    *,
    to_addrs: list[str],
    subject: str,
    plain: str,
    html: str,
    from_email: str | None = None,
    from_name: str = "Tatvic Labs",
) -> str:
    """RFC 822 message with headers that Gmail/Workspace treat as personal mail, not bulk."""
    if not to_addrs:
        raise ValueError("to_addrs is required")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = ", ".join(to_addrs)
    if from_email:
        msg["From"] = formataddr((from_name, from_email))
        msg["Reply-To"] = from_email
    msg["Date"] = formatdate(localtime=False)
    domain = (from_email.split("@", 1)[-1] if from_email else "tatvic.com")
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["MIME-Version"] = "1.0"
    # Keep this off bulk/list headers (Precedence, List-Unsubscribe) — those land in Promotions/Spam.
    msg.set_content(plain, charset="utf-8")
    msg.add_alternative(html, subtype="html", charset="utf-8")
    return msg.as_string()


def gmail_client_secrets_path() -> Path | None:
    settings = get_settings()
    raw = (settings.gmail_client_secrets_file or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None


def gmail_token_path() -> Path:
    settings = get_settings()
    raw = (settings.gmail_token_file or "").strip() or "data/secrets/gmail_token.json"
    return Path(raw).expanduser()


def load_gmail_credentials() -> tuple[Any | None, str | None]:
    """Return (credentials, failure_status).

    failure_status is one of:
    - ``gmail_not_configured`` — client secrets missing
    - ``gmail_not_authorized`` — token missing or refresh failed
    - ``None`` when credentials are usable
    """
    secrets = gmail_client_secrets_path()
    if secrets is None:
        return None, "gmail_not_configured"

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None, "gmail_not_configured"

    token_path = gmail_token_path()
    creds: Any | None = None
    if token_path.is_file():
        try:
            creds = Credentials.from_authorized_user_file(
                str(token_path),
                SCOPES,
            )
        except Exception:
            return None, "gmail_not_authorized"

    if creds and creds.valid:
        return creds, None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            return creds, None
        except Exception:
            return None, "gmail_not_authorized"

    return None, "gmail_not_authorized"


def send_gmail_raw_message(*, raw_rfc822: str) -> dict[str, Any]:
    """Send a pre-built RFC822 message via Gmail API as the authorized user."""
    creds, status = load_gmail_credentials()
    if creds is None:
        return {
            "sent": False,
            "status": status or "gmail_not_configured",
            "note": (
                "CEO report was saved locally. Configure Gmail OAuth "
                "(client secrets + token) to send via Gmail API. "
                "Run scripts/gmail_oauth_setup.py once."
            ),
        }

    try:
        from googleapiclient.discovery import build
    except ImportError:
        return {
            "sent": False,
            "status": "gmail_not_configured",
            "note": (
                "google-api-python-client is not installed. "
                "Report was saved locally."
            ),
        }

    encoded = base64.urlsafe_b64encode(raw_rfc822.encode("utf-8")).decode("ascii")
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": encoded})
            .execute()
        )
    except Exception as exc:
        return {
            "sent": False,
            "status": "gmail_send_failed",
            "note": f"Gmail API send failed: {exc}",
        }

    return {
        "sent": True,
        "status": "sent",
        "message_id": sent.get("id"),
    }
