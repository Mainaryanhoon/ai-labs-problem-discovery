#!/usr/bin/env python3
"""One-time Gmail OAuth setup for CEO report send (MASTER §7.1).

Prerequisites:
  1. Google Cloud project with Gmail API enabled
  2. OAuth client (Desktop app) JSON downloaded locally
  3. Set GMAIL_CLIENT_SECRETS_FILE (and optional GMAIL_TOKEN_FILE) in .env

Usage (from repo root):

    .venv/bin/python scripts/gmail_oauth_setup.py
    # or: source .venv/bin/activate && python scripts/gmail_oauth_setup.py

Opens a browser consent screen, then writes a refresh token to
GMAIL_TOKEN_FILE (default data/secrets/gmail_token.json). Never commit tokens.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.services.gmail_send import SCOPES, gmail_token_path


def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    secrets = (settings.gmail_client_secrets_file or "").strip()
    if not secrets:
        print(
            "Set GMAIL_CLIENT_SECRETS_FILE in .env to your OAuth client JSON.",
            file=sys.stderr,
        )
        return 1
    secrets_path = Path(secrets).expanduser()
    if not secrets_path.is_file():
        print(f"Client secrets not found: {secrets_path}", file=sys.stderr)
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path = gmail_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Wrote Gmail token to {token_path}")
    print("Scope: gmail.send only. Token file must stay gitignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
