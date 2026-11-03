from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services import gmail_send


def test_parse_recipient_emails_splits_and_dedupes():
    assert gmail_send.parse_recipient_emails(
        "aryan@tatvic.com, Khilav@tatvic.com; aryan@tatvic.com"
    ) == ["aryan@tatvic.com", "Khilav@tatvic.com"]
    assert gmail_send.parse_recipient_emails("  ") == []


def test_build_internal_rfc822_has_inbox_headers():
    raw = gmail_send.build_internal_rfc822(
        to_addrs=["aryan@tatvic.com", "khilav@tatvic.com"],
        subject="Tatvic Labs weekly leads — 02 Nov 2026",
        plain="Internal Tatvic briefing\n\n2 new leads this week.",
        html="<html><body><p>2 new leads this week.</p></body></html>",
        from_email="labs@tatvic.com",
        from_name="Tatvic Labs",
    )
    assert "From: Tatvic Labs <labs@tatvic.com>" in raw
    assert "To: aryan@tatvic.com, khilav@tatvic.com" in raw
    assert "Date:" in raw
    assert "Message-ID:" in raw
    assert "List-Unsubscribe:" not in raw
    assert "Precedence:" not in raw
    assert "text/plain" in raw
    assert "text/html" in raw


def test_load_credentials_missing_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_FILE", "")
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(tmp_path / "token.json"))
    from app.config import get_settings

    get_settings.cache_clear()
    creds, status = gmail_send.load_gmail_credentials()
    assert creds is None
    assert status == "gmail_not_configured"
    get_settings.cache_clear()


def test_load_credentials_missing_token(tmp_path, monkeypatch):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_FILE", str(secrets))
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(tmp_path / "missing_token.json"))
    from app.config import get_settings

    get_settings.cache_clear()
    creds, status = gmail_send.load_gmail_credentials()
    assert creds is None
    assert status == "gmail_not_authorized"
    get_settings.cache_clear()


def test_send_gmail_raw_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_FILE", str(tmp_path / "nope.json"))
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(tmp_path / "token.json"))
    from app.config import get_settings

    get_settings.cache_clear()
    result = gmail_send.send_gmail_raw_message(raw_rfc822="Subject: x\n\nbody")
    assert result["sent"] is False
    assert result["status"] == "gmail_not_configured"
    get_settings.cache_clear()


def test_send_gmail_raw_success(tmp_path, monkeypatch):
    secrets = tmp_path / "client_secret.json"
    secrets.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_FILE", str(secrets))
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(tmp_path / "token.json"))
    from app.config import get_settings

    get_settings.cache_clear()

    fake_creds = MagicMock()
    fake_creds.valid = True
    service = MagicMock()
    service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": "abc123",
    }

    with (
        patch.object(
            gmail_send,
            "load_gmail_credentials",
            return_value=(fake_creds, None),
        ),
        patch(
            "googleapiclient.discovery.build",
            return_value=service,
        ),
    ):
        result = gmail_send.send_gmail_raw_message(
            raw_rfc822="To: ceo@example.com\nSubject: t\n\nhello",
        )

    assert result == {
        "sent": True,
        "status": "sent",
        "message_id": "abc123",
    }
    send_call = service.users.return_value.messages.return_value.send
    assert send_call.call_args.kwargs["userId"] == "me"
    assert "raw" in send_call.call_args.kwargs["body"]
    get_settings.cache_clear()


def test_no_smtplib_in_ceo_report_module():
    import inspect

    import app.services.ceo_report as ceo_report

    source = Path(inspect.getfile(ceo_report)).read_text(encoding="utf-8")
    assert "smtplib" not in source
    assert "smtp_host" not in source
    assert "send_gmail_raw_message" in source
