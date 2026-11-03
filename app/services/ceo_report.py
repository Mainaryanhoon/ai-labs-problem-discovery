"""Build monthly Top-6 CEO report; optional email via Gmail API only.

MASTER_SPECIFICATION §7.1: SMTP is out of contract. Local HTML/text/MD under
``data/reports/`` is always persisted whether or not mail is sent.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.gmail_send import send_gmail_raw_message
from app.services.rankings import (
    create_monthly_snapshot,
    get_monthly_snapshot,
    mark_monthly_email,
)


def render_ceo_report_html(snapshot: dict[str, Any]) -> str:
    settings = get_settings()
    base = settings.report_public_base_url.rstrip("/")
    month = snapshot["month_start"]
    month_label = (
        month.strftime("%B %Y") if isinstance(month, date) else str(month)
    )
    rows = []
    for item in snapshot.get("items", []):
        detail = f"{base}/#/opportunities/{item['cluster_id']}"
        angle = (item.get("offering_angle") or "undetermined").replace("_", " ")
        components = item.get("components") or {}
        component_bits = ", ".join(
            f"{key.replace('_', ' ')} {value}/5"
            for key, value in components.items()
        )
        rows.append(
            f"""
            <tr>
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;vertical-align:top;">
                <div style="font-size:12px;color:#6b7280;">#{item['rank']} · score {item['score']:.0f} · {item.get('evidence_level')}</div>
                <div style="font-size:16px;font-weight:600;margin:4px 0;">{item['title']}</div>
                <div style="font-size:13px;color:#374151;">{item.get('section') or item.get('category') or 'Section TBD'}</div>
                <div style="font-size:13px;margin-top:8px;"><strong>Offering:</strong> {angle}</div>
                <div style="font-size:13px;"><strong>Smallest v1:</strong> {item.get('smallest_first_version') or 'TBD'}</div>
                <div style="font-size:13px;"><strong>First client:</strong> {item.get('first_client_type') or 'TBD'}</div>
                <div style="font-size:13px;"><strong>Failure risk:</strong> {item.get('failure_risk_line') or 'TBD'}</div>
                <div style="font-size:12px;color:#6b7280;margin-top:6px;">{component_bits}</div>
                <div style="margin-top:8px;"><a href="{detail}">Open evidence in dashboard</a></div>
              </td>
            </tr>
            """
        )
    body_rows = "".join(rows) or (
        "<tr><td style='padding:16px;'>No Tatvic-ready clusters met the "
        "Top-6 bar this month (need ≥3 items, ≥2 authors, multi-source "
        "evidence, and a clear offering angle).</td></tr>"
    )
    intended = settings.ceo_email.strip() or "not configured"
    return f"""<!DOCTYPE html>
<html><body style="font-family:Georgia,serif;background:#f8fafc;padding:24px;">
  <div style="max-width:720px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;padding:28px;">
    <p style="letter-spacing:.08em;text-transform:uppercase;font-size:11px;color:#6b7280;margin:0;">Tatvic AI Labs</p>
    <h1 style="margin:8px 0 4px;font-size:28px;">Monthly Top {snapshot.get('top_k', 6)} problems — {month_label}</h1>
    <p style="color:#4b5563;font-size:14px;">
      Intended recipient: <strong>{intended}</strong>.
      Weight version <code>{snapshot.get('weight_version') or 'n/a'}</code>.
      Prioritisation for offerings investigation — not market-size proof.
    </p>
    <table style="width:100%;border-collapse:collapse;margin-top:20px;">{body_rows}</table>
    <p style="font-size:12px;color:#9ca3af;margin-top:24px;">
      Generated automatically by ai-labs-problem-discovery.
    </p>
  </div>
</body></html>"""


def render_ceo_report_text(snapshot: dict[str, Any]) -> str:
    settings = get_settings()
    month = snapshot["month_start"]
    lines = [
        f"Tatvic Monthly Top {snapshot.get('top_k', 6)} — {month}",
        f"Intended recipient: {settings.ceo_email or 'not configured'}",
        f"Weight version: {snapshot.get('weight_version')}",
        "",
    ]
    for item in snapshot.get("items", []):
        lines.extend([
            f"#{item['rank']}  {item['title']}  (score {item['score']})",
            f"  Section: {item.get('section')}",
            f"  Offering: {item.get('offering_angle')}",
            f"  v1: {item.get('smallest_first_version')}",
            f"  Client: {item.get('first_client_type')}",
            f"  Risk: {item.get('failure_risk_line')}",
            "",
        ])
    if len(lines) <= 4:
        lines.append("No Tatvic-ready clusters met the Top-6 bar this month.")
    return "\n".join(lines)


def persist_ceo_report(
    snapshot: dict[str, Any],
    *,
    html: str,
    text: str,
) -> dict[str, str]:
    settings = get_settings()
    out_dir = Path(settings.report_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    month = snapshot["month_start"]
    stamp = (
        month.isoformat()
        if isinstance(month, date)
        else str(month)
    )
    generated = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    html_path = out_dir / f"ceo-top6-{stamp}-{generated}.html"
    text_path = out_dir / f"ceo-top6-{stamp}-{generated}.txt"
    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    latest_html = out_dir / "ceo-top6-latest.html"
    latest_text = out_dir / "ceo-top6-latest.txt"
    latest_html.write_text(html, encoding="utf-8")
    latest_text.write_text(text, encoding="utf-8")
    # Monthly CEO markdown only — never overwrite the canonical discovery
    # report at data/reports/opportunities_YYYY-MM-DD.md (owned by
    # app.services.discovery_report).
    md_path = out_dir / f"ceo-top6-{stamp}.md"
    latest_md = out_dir / "ceo-top6-latest.md"
    markdown = render_opportunities_markdown(snapshot)
    md_path.write_text(markdown, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    return {
        "html_path": str(html_path),
        "text_path": str(text_path),
        "latest_html": str(latest_html),
        "latest_text": str(latest_text),
        "ceo_markdown": str(md_path),
        "latest_ceo_markdown": str(latest_md),
        # Backward-compatible alias for CEO markdown (not the discovery report).
        "opportunities_md": str(md_path),
    }


def render_opportunities_markdown(snapshot: dict[str, Any]) -> str:
    """Monthly CEO Top-K markdown (not the canonical discovery report)."""
    month = snapshot.get("month_start")
    lines = [
        f"# Tatvic CEO Top-{snapshot.get('top_k', 6)} — {month}",
        "",
        f"- Weight version: `{snapshot.get('weight_version') or 'v0.1'}`",
        f"- Top K: {snapshot.get('top_k', 6)}",
        "- Status: monthly CEO shortlist draft",
        "- Note: canonical discovery report is "
        "`data/reports/opportunities_YYYY-MM-DD.md` (pipeline-owned)",
        "",
        "## Top opportunities",
        "",
    ]
    items = snapshot.get("items") or []
    if not items:
        lines.append(
            "No clusters met the Top bar (need ≥3 items, ≥2 authors, "
            "in-scope section, and offering angle fields)."
        )
        return "\n".join(lines)
    for item in items:
        lines.extend([
            f"### #{item.get('rank')} — {item.get('title')}",
            f"- Section: {item.get('section')}",
            f"- Score: {item.get('score')} / 100",
            f"- Evidence level: {item.get('evidence_level')}",
            f"- Offering angle: {item.get('offering_angle')}",
            f"- Smallest first version: {item.get('smallest_first_version')}",
            f"- First client type: {item.get('first_client_type')}",
            f"- Why it might fail: {item.get('failure_risk_line')}",
            f"- Cluster id: {item.get('cluster_id')}",
            "",
        ])
    return "\n".join(lines)


def send_ceo_email(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Attempt Gmail API send when OAuth is configured (MASTER §7.1)."""
    settings = get_settings()
    if not settings.ceo_email.strip():
        return {
            "sent": False,
            "status": "ceo_email_not_configured",
            "note": (
                "CEO_EMAIL is unset; report was saved locally. "
                "Email is optional and never required for discovery done."
            ),
        }

    month = snapshot["month_start"]
    month_label = (
        month.strftime("%B %Y") if isinstance(month, date) else str(month)
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Tatvic Top {snapshot.get('top_k', 6)} problems — {month_label}"
    # Gmail API sends as the OAuth user ("me"); To is the CEO recipient.
    msg["To"] = settings.ceo_email
    msg.attach(MIMEText(render_ceo_report_text(snapshot), "plain"))
    msg.attach(MIMEText(render_ceo_report_html(snapshot), "html"))

    result = send_gmail_raw_message(raw_rfc822=msg.as_string())
    return {
        **result,
        "to": settings.ceo_email,
    }


def generate_and_email_monthly_report(
    db: Session,
    summarize,
    *,
    month_start: date | None = None,
    send_email: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    snapshot = create_monthly_snapshot(
        db,
        summarize,
        month_start=month_start,
        force=force,
    )
    html = render_ceo_report_html(snapshot)
    text = render_ceo_report_text(snapshot)
    files = persist_ceo_report(snapshot, html=html, text=text)

    email_result: dict[str, Any] = {"sent": False, "status": "skipped"}
    if send_email:
        email_result = send_ceo_email(snapshot)
        if not email_result.get("sent"):
            email_result = {
                **email_result,
                "files": files,
            }
        mark_monthly_email(
            db,
            snapshot["month_start"],
            status=email_result["status"],
        )
        refreshed = get_monthly_snapshot(db, snapshot["month_start"])
        if refreshed is not None:
            snapshot = refreshed
    else:
        email_result = {
            "sent": False,
            "status": "report_saved_only",
            "files": files,
        }
        mark_monthly_email(
            db,
            snapshot["month_start"],
            status="report_saved_only",
        )

    return {
        "snapshot": snapshot,
        "html": html,
        "text": text,
        "files": files,
        "email": email_result,
        "intended_recipient": get_settings().ceo_email or None,
    }
