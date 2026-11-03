from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.services.ceo_report import (
    persist_ceo_report,
    render_ceo_report_html,
    render_ceo_report_text,
    send_ceo_email,
)
from app.services.discovery_report import discovery_report_path


def _snapshot():
    return {
        "month_start": date(2026, 10, 1),
        "top_k": 6,
        "weight_version": "v0.1",
        "items": [
            {
                "rank": 1,
                "cluster_id": 42,
                "title": "GA4 purchase reconciliation drift",
                "section": "Analytics",
                "score": 63.8,
                "evidence_level": "moderate",
                "offering_angle": "upgrade_existing_offering",
                "smallest_first_version": "Sheets-assisted audit agent",
                "first_client_type": "eCommerce analytics lead",
                "failure_risk_line": "Native GA4 fix may shrink demand",
                "components": {"demand_strength": 4},
            }
        ],
    }


def test_ceo_report_renders_top_six_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("CEO_EMAIL", "ceo@tatvic.example")
    # Force unset OAuth paths even if local .env has them.
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_FILE", "")
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(tmp_path / "missing_token.json"))
    from app.config import get_settings

    get_settings.cache_clear()

    snapshot = _snapshot()
    html = render_ceo_report_html(snapshot)
    text = render_ceo_report_text(snapshot)
    assert "GA4 purchase reconciliation drift" in html
    assert "upgrade existing offering" in html
    assert "Sheets-assisted audit agent" in html
    assert "#1" in text
    assert "v0.1" in html
    assert "ceo@tatvic.example" in html

    files = persist_ceo_report(snapshot, html=html, text=text)
    assert Path(files["latest_html"]).exists()
    assert Path(files["ceo_markdown"]).name == "ceo-top6-2026-10-01.md"
    assert Path(files["latest_ceo_markdown"]).name == "ceo-top6-latest.md"
    assert Path(files["opportunities_md"]) == Path(files["ceo_markdown"])

    email = send_ceo_email(snapshot)
    assert email["sent"] is False
    assert email["status"] == "gmail_not_configured"
    assert email["to"] == "ceo@tatvic.example"
    get_settings.cache_clear()


def test_ceo_persist_does_not_overwrite_discovery_report(tmp_path, monkeypatch):
    """Canonical discovery MD must remain owned by discovery_report."""
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()

    discovery = discovery_report_path(
        run_date=date(2026, 10, 29),
        output_dir=tmp_path,
    )
    sentinel = (
        "# Tatvic opportunities — 2026-10-29\n\n"
        "CANONICAL_DISCOVERY_SENTINEL\n"
    )
    discovery.write_text(sentinel, encoding="utf-8")

    files = persist_ceo_report(
        _snapshot(),
        html=render_ceo_report_html(_snapshot()),
        text=render_ceo_report_text(_snapshot()),
    )

    assert discovery.read_text(encoding="utf-8") == sentinel
    ceo_md = Path(files["ceo_markdown"])
    assert ceo_md.exists()
    assert ceo_md != discovery
    assert "CEO Top-" in ceo_md.read_text(encoding="utf-8")
    assert list(tmp_path.glob("opportunities_*.md")) == [discovery]
    get_settings.cache_clear()


def test_send_ceo_email_uses_gmail_not_smtp(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("CEO_EMAIL", "ceo@tatvic.example")
    from app.config import get_settings

    get_settings.cache_clear()

    with patch(
        "app.services.ceo_report.send_gmail_raw_message",
        return_value={
            "sent": True,
            "status": "sent",
            "message_id": "msg-1",
        },
    ) as mock_send:
        result = send_ceo_email(_snapshot())

    assert result["sent"] is True
    assert result["status"] == "sent"
    assert result["to"] == "ceo@tatvic.example"
    assert mock_send.call_count == 1
    raw = mock_send.call_args.kwargs["raw_rfc822"]
    assert "To: ceo@tatvic.example" in raw
    assert "multipart/alternative" in raw
    get_settings.cache_clear()


def test_send_ceo_email_without_recipient(monkeypatch):
    monkeypatch.delenv("CEO_EMAIL", raising=False)
    monkeypatch.setenv("CEO_EMAIL", "")
    from app.config import get_settings

    get_settings.cache_clear()
    result = send_ceo_email(_snapshot())
    assert result["sent"] is False
    assert result["status"] == "ceo_email_not_configured"
    get_settings.cache_clear()
