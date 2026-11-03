"""Weekly run: plain Python, no agent. collect -> extract leads -> report -> funnel.

cron:  0 7 * * MON  cd /path/to/repo && .venv/bin/python scripts/weekly_run.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, initialize_database  # noqa: E402
from app.leads import models  # noqa: F401,E402
from app.leads.llm import complete_json  # noqa: E402
from app.leads.report import generate_weekly_email_body, write_report  # noqa: E402
from app.leads.service import run_extraction  # noqa: E402
from app.services.gmail_send import (  # noqa: E402
    build_internal_rfc822,
    gmail_authenticated_email,
    load_gmail_credentials,
    parse_recipient_emails,
    send_gmail_raw_message,
)
from app.ingestion import collect_hacker_news, collect_stack_exchange  # noqa: E402
from app.social_ingestion import collect_reddit, collect_x  # noqa: E402
from app.youtube_ingestion import collect_youtube  # noqa: E402


def _flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def _safe_collect(
    *,
    source: str,
    collector: Callable[..., dict[str, Any]],
    collect_limit: int,
    db,
    statuses: list[dict[str, Any]],
) -> int:
    try:
        result = collector(limit=collect_limit, db=db)
    except Exception as exc:
        statuses.append({
            "source": source,
            "status": "unavailable",
            "error": type(exc).__name__,
            "detail": str(getattr(exc, "detail", exc))[:300],
            "created": 0,
        })
        return 0
    if not isinstance(result, dict):
        statuses.append({
            "source": source,
            "status": "unavailable",
            "error": "invalid_result",
            "created": 0,
        })
        return 0
    created = int(result.get("created") or 0)
    statuses.append({
        "source": result.get("source") or source,
        "status": result.get("status") or result.get("fetch_status") or "fetched",
        "created": created,
        "message": result.get("message"),
    })
    return created


def _send_weekly_email(
    db,
    funnel: dict[str, Any],
    *,
    shown,
    repeats,
    watch,
) -> dict[str, Any]:
    get_settings.cache_clear()
    settings = get_settings()
    recipients = parse_recipient_emails(
        os.getenv("CEO_EMAIL") or settings.ceo_email,
    )
    if not recipients:
        return {"sent": False, "status": "skipped_no_recipient"}

    plain, html_body, _summary = generate_weekly_email_body(
        db,
        funnel,
        top_n=5,
        shown=shown,
        repeats=repeats,
        watch=watch,
    )

    creds, _status = load_gmail_credentials()
    from_email = gmail_authenticated_email(creds) if creds is not None else None
    today_label = date.today().strftime("%d %b %Y")
    raw = build_internal_rfc822(
        to_addrs=recipients,
        subject=f"Tatvic Labs weekly leads — {today_label}",
        plain=plain,
        html=html_body,
        from_email=from_email,
        from_name="Tatvic Labs",
    )

    result = send_gmail_raw_message(raw_rfc822=raw)
    return {**result, "to": ", ".join(recipients)}


def main() -> int:
    initialize_database()
    db = SessionLocal()
    try:
        from app.article_ingestion import collect_articles

        collect_limit = int(os.getenv("LEADS_COLLECT_LIMIT", "80"))
        collected = collect_articles(collect_limit, db, ignore_cursor=True)
        created_total = int(collected.get("created") or 0)
        feed_statuses: list[dict[str, Any]] = list(collected.get("feed_statuses") or [])
        extract_sources = ["articles"]

        created_total += _safe_collect(
            source="hacker_news",
            collector=collect_hacker_news,
            collect_limit=collect_limit,
            db=db,
            statuses=feed_statuses,
        )
        extract_sources.append("Hacker News")

        created_total += _safe_collect(
            source="stack_exchange",
            collector=lambda limit, db: collect_stack_exchange(
                limit=limit,
                site="stackoverflow",
                db=db,
            ),
            collect_limit=collect_limit,
            db=db,
            statuses=feed_statuses,
        )
        extract_sources.append("Stack Exchange:stackoverflow")

        if _flag_enabled("ENABLE_REDDIT"):
            created_total += _safe_collect(
                source="reddit",
                collector=collect_reddit,
                collect_limit=collect_limit,
                db=db,
                statuses=feed_statuses,
            )
            extract_sources.append("Reddit")

        if _flag_enabled("ENABLE_X"):
            created_total += _safe_collect(
                source="x",
                collector=lambda limit, db: collect_x(limit=50, db=db),
                collect_limit=50,
                db=db,
                statuses=feed_statuses,
            )
            extract_sources.append("X")

        if _flag_enabled("ENABLE_YOUTUBE"):
            created_total += _safe_collect(
                source="youtube",
                collector=collect_youtube,
                collect_limit=collect_limit,
                db=db,
                statuses=feed_statuses,
            )
            extract_sources.append("youtube")

        funnel = run_extraction(
            db,
            complete_json,
            max_calls=int(os.getenv("LEADS_MAX_LLM_CALLS", "40")),
            sources=tuple(extract_sources),
        )
        funnel["collected"] = created_total
        funnel["feed_statuses"] = feed_statuses
        report, shown, repeats, watch = write_report(db, funnel, top_n=5)
        email_result = _send_weekly_email(
            db,
            funnel,
            shown=shown,
            repeats=repeats,
            watch=watch,
        )
        out = Path("data/reports")
        out.mkdir(parents=True, exist_ok=True)
        (out / f"funnel_{date.today().isoformat()}.json").write_text(
            json.dumps(funnel, indent=2, default=str),
        )
        print(f"report: {report} (Top 5 leads: {len(shown)})")
        if email_result.get("sent"):
            print(f"email: sent to {email_result.get('to')} (Top 5 leads)")
        elif email_result.get("status") == "skipped_no_recipient":
            print("email: skipped (CEO_EMAIL not set)")
        else:
            note = email_result.get("note") or ""
            print(f"email: {email_result.get('status')} {note}".strip())
        print(json.dumps({k: v for k, v in funnel.items() if k != "feed_statuses"}, indent=2, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
