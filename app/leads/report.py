"""Weekly leads report. 3-line summary first; says so when there is nothing."""
from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.leads.models import Lead
from app.leads.scoring import MIN_INDIA_RELEVANCE, MIN_LEAD_SCORE, WEIGHT_VERSION, WEIGHTS
from app.leads.service import is_rejected, mark_surfaced, select_for_report, top_rejection_reasons

NOTHING = "Nothing worth looking at this week."


def _delivery_label(delivery_type: str | None) -> str:
    value = (delivery_type or "unclear").casefold()
    if value == "product":
        return "Standalone Product"
    if value == "service":
        return "Service"
    return "Unclear"


def executive_summary_lines(
    shown: list[Lead],
    repeats: list[Lead],
    watch: list[Lead],
) -> tuple[str, str, str]:
    if shown:
        best = shown[0]
        line1 = (
            f"{len(shown)} new lead(s) this week. "
            f"Top lead is \"{best.title}\" ({best.score}/100)."
        )
        line2 = f"{len(repeats)} lead(s) suppressed; {len(watch)} on watchlist."
        line3 = (
            "These are leads from public sources, not validated demand; "
            "validate on sales calls."
        )
    else:
        line1 = "0 new leads this week."
        line2 = f"{len(repeats)} lead(s) suppressed; {len(watch)} on watchlist."
        line3 = "Check the funnel in the dashboard to see where candidates dropped."
    return line1, line2, line3


def _without_rejected(leads: list[Lead] | None) -> list[Lead]:
    return [lead for lead in (leads or []) if not is_rejected(lead)]


def _limit_top_n(
    shown: list[Lead],
    repeats: list[Lead],
    watch: list[Lead],
    top_n: int = 5,
) -> tuple[list[Lead], list[Lead], list[Lead]]:
    """Keep the Top 5 in the report; remaining eligible leads go to the watchlist."""
    shown = _without_rejected(shown)
    repeats = _without_rejected(repeats)
    watch = _without_rejected(watch)
    limit = max(1, int(top_n))
    overflow = list(shown[limit:])
    shown = list(shown[:limit])
    if overflow:
        watch = overflow + list(watch)
    return shown, list(repeats), list(watch)


def _http_urls(lead: Lead) -> list[str]:
    raw_extra = json.loads(lead.corroborating_urls_json or "[]")
    extra = raw_extra if isinstance(raw_extra, list) else []
    found: list[str] = []
    seen: set[str] = set()
    for candidate in [lead.source_url, *extra]:
        if not isinstance(candidate, str):
            continue
        url = candidate.strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if url in seen:
            continue
        seen.add(url)
        found.append(url)
    return found


def _block(lead: Lead) -> list[str]:
    comps = json.loads(lead.components_json)
    quotes = json.loads(lead.evidence_quotes_json)
    urls = _http_urls(lead)
    lines = [
        f"### Lead #{lead.id}: {lead.title}",
        f"- Score: **{lead.score}**/100 ({WEIGHT_VERSION}) | India relevance {comps.get('india_relevance')}/5 | "
        f"Delivery: **{lead.delivery_type}**",
        f"- Who pays: {lead.who_pays}" + (f" | Company: {lead.company}" if lead.company else ""),
        f"- Problem: {lead.problem}",
        f"- What to Build (v1): {_what_to_build(lead)}",
        f"- Why now: {lead.why_now}",
        f"- First customer route: {lead.first_customer_route}",
    ]
    if lead.tatvic_fit_note:
        lines.append(f"- Tatvic fit (scored, not a gate): {lead.tatvic_fit_note}")
    if quotes:
        lines.append(f"- Evidence: \"{quotes[0]}\"")
    if lead.india_quote:
        lines.append(f"- India link: \"{lead.india_quote}\"")
    if urls:
        lines.append("- Source Citations:")
        lines.extend(f"  - {url}" for url in urls)
    else:
        lines.append("- Source Citations: See attached source records.")
    lines.append(
        f"- Review: `python -m app.leads accept {lead.id} --reason \"...\"` or "
        f"`python -m app.leads reject {lead.id} --reason \"...\"`"
    )
    lines.append("")
    return lines


def _lead_quotes(lead: Lead) -> list[str]:
    raw = json.loads(lead.evidence_quotes_json or "[]")
    if not isinstance(raw, list):
        return []
    quotes = [q.strip() for q in raw if isinstance(q, str) and q.strip()]
    india_quote = getattr(lead, "india_quote", None)
    if isinstance(india_quote, str) and india_quote.strip() and india_quote.strip() not in quotes:
        quotes.append(india_quote.strip())
    return quotes


def _what_to_build(lead: Lead) -> str:
    smallest = getattr(lead, "smallest_first_version", None)
    if isinstance(smallest, str) and smallest.strip():
        return smallest.strip()
    return "Needs definition."


def _format_lead_email_plain(lead: Lead, *, rank: int) -> list[str]:
    quotes = _lead_quotes(lead)
    urls = _http_urls(lead)
    commercial = lead.who_pays + (f" ({lead.company})" if lead.company else "")
    lines = [
        f"{rank}. {lead.title} — {lead.score}/100",
        f"   Delivery type: {_delivery_label(lead.delivery_type)}",
        f"   Problem: {lead.problem}",
        f"   What to Build (v1): {_what_to_build(lead)}",
        "   Source Citations:",
    ]
    for url in urls:
        lines.append(f"     • {url}")
    if not urls:
        lines.append("     • See attached source records.")
    if quotes:
        lines.append("   Evidence:")
        for quote in quotes[:3]:
            lines.append(f'     • "{quote}"')
    lines.extend([
        f"   Commercial target: {commercial}",
        f"   Why now: {lead.why_now}",
        f"   First customer reach strategy: {lead.first_customer_route}",
        "",
    ])
    return lines


def _format_lead_email_html(lead: Lead, *, rank: int) -> str:
    quotes = _lead_quotes(lead)
    urls = _http_urls(lead)
    commercial = lead.who_pays + (f" ({html.escape(lead.company)})" if lead.company else "")
    if urls:
        url_items = "".join(
            f'<li><a href="{html.escape(url, quote=True)}">{html.escape(url)}</a></li>'
            for url in urls
        )
    else:
        url_items = "<li><em>See attached source records.</em></li>"
    quote_items = "".join(
        f"<li>&ldquo;{html.escape(q)}&rdquo;</li>" for q in quotes[:3]
    )
    evidence_html = (
        f"<p style=\"margin:0 0 8px;\"><strong>Evidence</strong></p>"
        f"<ul style=\"margin:0 0 12px;padding-left:20px;\">{quote_items}</ul>"
        if quote_items else ""
    )
    return f"""
<section style="margin:0 0 24px;padding:16px;border:1px solid #e2e8f0;border-radius:8px;">
  <h2 style="margin:0 0 8px;font-size:18px;color:#111111;">#{rank} {html.escape(lead.title)}</h2>
  <p style="margin:0 0 12px;font-size:14px;color:#444444;">Score: <strong>{lead.score}/100</strong> &nbsp;|&nbsp; Delivery: <strong>{html.escape(_delivery_label(lead.delivery_type))}</strong></p>
  <p style="margin:0 0 8px;"><strong>Problem</strong><br>{html.escape(lead.problem)}</p>
  <p style="margin:0 0 8px;"><strong>What to Build (v1)</strong><br>{html.escape(lead.smallest_first_version or _what_to_build(lead))}</p>
  <p style="margin:0 0 8px;"><strong>Source Citations</strong></p>
  <ul style="margin:0 0 12px;padding-left:20px;">{url_items}</ul>
  {evidence_html}
  <p style="margin:0 0 8px;"><strong>Commercial target</strong><br>{html.escape(commercial)}</p>
  <p style="margin:0 0 8px;"><strong>Why now</strong><br>{html.escape(lead.why_now or "")}</p>
  <p style="margin:0;"><strong>First customer reach strategy</strong><br>{html.escape(lead.first_customer_route or "")}</p>
</section>
"""


def generate_weekly_email_body(
    db: Session,
    funnel: dict | None,
    top_n: int = 5,
    *,
    shown: list[Lead] | None = None,
    repeats: list[Lead] | None = None,
    watch: list[Lead] | None = None,
    today: date | None = None,
) -> tuple[str, str, tuple[str, str, str]]:
    """Return (plain_text, html_body, executive_summary_3_lines).

    Pass ``shown`` / ``repeats`` / ``watch`` from ``write_report`` so the email
    matches the markdown file before idea-log suppression changes selection.
    """
    today = today or date.today()
    if shown is None or repeats is None or watch is None:
        fresh, rep, wch = select_for_report(db)
        shown = shown if shown is not None else fresh
        repeats = rep if repeats is None else repeats
        watch = wch if watch is None else watch
    shown, repeats, watch = _limit_top_n(shown, repeats, watch, top_n=top_n)

    summary = executive_summary_lines(shown, repeats, watch)
    intro = "Internal Tatvic briefing — not a newsletter."
    plain_parts = [intro, "", summary[0], summary[1], summary[2], "", "Top 5 leads", ""]
    html_parts = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>Tatvic Labs weekly leads — {today.isoformat()}</title></head>",
        "<body style=\"font-family:Arial,Helvetica,sans-serif;color:#111111;"
        "max-width:640px;margin:0 auto;padding:24px;background:#ffffff;\">",
        f"<div style=\"display:none;max-height:0;overflow:hidden;\">{html.escape(summary[0])}</div>",
        "<p style=\"font-size:13px;color:#444444;margin:0 0 16px;\">Internal Tatvic briefing</p>",
        "<h1 style=\"color:#111111;font-size:20px;margin:0 0 16px;\">Weekly leads — Top 5</h1>",
        f"<p style=\"font-size:15px;line-height:1.5;color:#222222;\">{html.escape(summary[0])}</p>",
        f"<p style=\"font-size:15px;line-height:1.5;color:#222222;\">{html.escape(summary[1])}</p>",
        f"<p style=\"font-size:15px;line-height:1.5;color:#222222;\">{html.escape(summary[2])}</p>",
        "<hr style=\"border:none;border-top:1px solid #dddddd;margin:24px 0;\" />",
    ]

    if shown:
        for index, lead in enumerate(shown, start=1):
            plain_parts.extend(_format_lead_email_plain(lead, rank=index))
            html_parts.append(_format_lead_email_html(lead, rank=index))
    else:
        plain_parts.append("No leads met the score and India threshold this week.")
        html_parts.append("<p><em>No leads met the score and India threshold this week.</em></p>")

    if funnel is not None:
        plain_parts.extend([
            "",
            "Funnel (this run)",
            f"- eligible: {funnel.get('eligible', 0)}",
            f"- saved_new: {funnel.get('saved_new', 0)}",
            f"- collected: {funnel.get('collected', 0)}",
        ])
        html_parts.append(
            f"<p style=\"font-size:12px;color:#64748b;margin-top:24px;\">"
            f"Funnel: eligible {funnel.get('eligible', 0)}, "
            f"saved_new {funnel.get('saved_new', 0)}, "
            f"collected {funnel.get('collected', 0)}"
            f"</p>"
        )

    html_parts.append(
        f"<p style=\"font-size:11px;color:#94a3b8;margin-top:32px;\">"
        f"Report date {today.isoformat()} · scoring {WEIGHT_VERSION}</p></body></html>"
    )
    return "\n".join(plain_parts), "".join(html_parts), summary


def render_report(
    db: Session,
    funnel: dict | None,
    *,
    today: date | None = None,
    top_n: int = 5,
) -> tuple[str, list[Lead], list[Lead], list[Lead]]:
    today = today or date.today()
    fresh, repeats, watch = select_for_report(db)
    shown, repeats, watch = _limit_top_n(fresh, repeats, watch, top_n=top_n)
    line1, line2, line3 = executive_summary_lines(shown, repeats, watch)
    out = [line1, line2, line3, "", f"# Leads report - {today.isoformat()}", "",
           f"Scoring {WEIGHT_VERSION}; lead threshold score >= {MIN_LEAD_SCORE:.0f} and India relevance >= {MIN_INDIA_RELEVANCE}.",
           "Showing Top 5 scored leads.", ""]
    out.append("## Top 5 leads")
    out.append("")
    if shown:
        for lead in shown:
            out.extend(_block(lead))
    else:
        out.extend([NOTHING, ""])
    out.append("## Watchlist (below threshold, not recommended)")
    out.append("")
    if watch:
        for lead in watch:
            comps = json.loads(lead.components_json)
            out.append(f"- #{lead.id} {lead.title} - {lead.score}/100, India {comps.get('india_relevance')}/5, {lead.delivery_type}")
    else:
        out.append("- none")
    out.append("")
    reasons = top_rejection_reasons(db)
    if reasons:
        out.append("## What reviewers rejected recently")
        out.append("")
        out.extend(f"- {r} ({n}x)" for r, n in reasons)
        out.append("")
    if funnel is not None:
        out.append("## Funnel (this run)")
        out.append("")
        for k in ("eligible", "llm_called", "model_not_lead", "validation_failed", "llm_errors", "saved_new", "merged_into_existing"):
            out.append(f"- {k}: {funnel.get(k, 0)}")
        out.append(f"- by publisher: {funnel.get('by_host', {})}")
        out.append(f"- drop reasons: {funnel.get('fail_reasons', {})}")
        if "collected" in funnel:
            out.append(f"- collected from feeds: {funnel['collected']}")
        out.append("")
    out.append(f"Weights: {WEIGHTS}")
    return "\n".join(out) + "\n", shown, repeats, watch


def write_report(
    db: Session,
    funnel: dict | None,
    *,
    out_dir: str = "data/reports",
    today: date | None = None,
    top_n: int = 5,
) -> tuple[Path, list[Lead], list[Lead], list[Lead]]:
    today = today or date.today()
    text, shown, repeats, watch = render_report(db, funnel, today=today, top_n=top_n)
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"leads_{today.isoformat()}.md"
    target.write_text(text, encoding="utf-8")
    mark_surfaced(db, shown)
    return target, shown, repeats, watch
