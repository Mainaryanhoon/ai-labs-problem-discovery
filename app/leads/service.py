"""Run lead extraction, persist leads, idea log, feedback."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.leads import extract
from app.leads.models import IdeaLog, Lead, LeadFeedback, LeadProcessed
from app.leads.scoring import (
    MIN_INDIA_RELEVANCE,
    MIN_LEAD_SCORE,
    RESURFACE_SCORE_DELTA,
    WEIGHT_VERSION,
    compute_score,
    india_marker_count,
    jaccard,
    title_tokens,
)
from app.models import Submission

LEAD_SOURCES = ("articles",)   # Reddit/X are excluded unless explicitly enabled by the caller


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _host(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").removeprefix("www.")


def fingerprint_for(title: str) -> str:
    import hashlib

    toks = sorted(set(title_tokens(title)))[:8]
    return hashlib.sha1(" ".join(toks).encode()).hexdigest()[:20]


def _find_existing(db: Session, title: str) -> Lead | None:
    fp = fingerprint_for(title)
    hit = db.scalar(select(Lead).where(Lead.fingerprint == fp))
    if hit:
        return hit
    toks = title_tokens(title)
    for lead in db.scalars(select(Lead)).all():
        if jaccard(toks, title_tokens(lead.title)) >= 0.6:
            return lead
    return None


def recent_rejected_themes(db: Session, limit: int = 10) -> list[str]:
    """Latest unique reviewer rejection reasons (and rejected titles as fallback)."""
    themes: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        text = " ".join((raw or "").split()).strip()
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        themes.append(text[:240])

    reasons = db.scalars(
        select(LeadFeedback.reason)
        .where(LeadFeedback.decision == "rejected")
        .order_by(LeadFeedback.created_at.desc())
    ).all()
    for reason in reasons:
        _add(reason)
        if len(themes) >= limit:
            return themes

    titles = db.scalars(
        select(Lead.title).where(Lead.status == "rejected").order_by(Lead.last_seen.desc())
    ).all()
    for title in titles:
        _add(title)
        if len(themes) >= limit:
            break
    return themes


def feedback_adjustment(db: Session, host: str) -> float:
    """Learn from reviewers: net accepted/rejected per publisher host."""
    if not host:
        return 0.0
    rows = db.execute(
        select(Lead.source_host, LeadFeedback.decision).join(LeadFeedback, LeadFeedback.lead_id == Lead.id)
    ).all()
    acc = sum(1 for h, d in rows if h == host and d == "accepted")
    rej = sum(1 for h, d in rows if h == host and d == "rejected")
    return float(max(-10, min(6, 2 * acc - 3 * rej)))


def run_extraction(
    db: Session,
    llm,                       # callable(system, user) -> str
    *,
    max_calls: int = 40,
    lookback_days: int = 60,
    sources: tuple[str, ...] = LEAD_SOURCES,
) -> dict:
    """Funnel: eligible -> llm_called -> is_lead -> verified -> saved. Per-host breakdown."""
    cutoff = _now() - timedelta(days=lookback_days)
    done = set(db.scalars(select(LeadProcessed.submission_id)).all())
    subs = [
        s for s in db.scalars(select(Submission).where(Submission.source_name.in_(sources))).all()
        if s.id not in done and (s.created_at is None or _aware(s.created_at) >= cutoff)
    ]
    # Spend calls on the most India-relevant, then newest, items first.
    subs.sort(key=lambda s: (-india_marker_count(s.text), -(_aware(s.created_at).timestamp() if s.created_at else 0)))
    rejected_themes = recent_rejected_themes(db, limit=10)
    funnel = {
        "eligible": len(subs), "llm_called": 0, "model_not_lead": 0, "validation_failed": 0,
        "llm_errors": 0, "saved_new": 0, "merged_into_existing": 0,
        "by_host": Counter(), "fail_reasons": Counter(),
    }
    for sub in subs[:max_calls]:
        host = _host(sub.source_url)
        funnel["by_host"][host] += 1
        try:
            raw = llm(
                extract.SYSTEM_PROMPT,
                extract.build_user_prompt(
                    sub.text,
                    sub.source_url,
                    rejected_themes=rejected_themes,
                ),
            )
            funnel["llm_called"] += 1
            parsed = extract.parse_json(raw)
        except Exception as exc:  # network, quota, bad JSON: record, never crash the run
            funnel["llm_errors"] += 1
            funnel["fail_reasons"][type(exc).__name__] += 1
            if "rate" in str(exc).lower() or "quota" in str(exc).lower():
                break
            continue
        clean, reason = extract.validate_lead(parsed, sub.text)
        if clean is None:
            key = "model_not_lead" if reason == "model_not_lead" else "validation_failed"
            funnel[key] += 1
            funnel["fail_reasons"][reason] += 1
            db.add(LeadProcessed(submission_id=sub.id, outcome=reason))
            db.commit()
            continue
        existing = _find_existing(db, clean["title"])
        if existing:
            urls = json.loads(existing.corroborating_urls_json or "[]")
            if sub.source_url and sub.source_url not in urls and sub.source_url != existing.source_url:
                urls.append(sub.source_url)
            existing.corroborating_urls_json = json.dumps(urls)
            existing.last_seen = _now()
            comps = json.loads(existing.components_json)
            if urls:
                comps["evidence_quality"] = 5
                existing.components_json = json.dumps(comps)
                existing.score, _ = compute_score(comps, feedback_adjustment(db, existing.source_host or ""))
            funnel["merged_into_existing"] += 1
        else:
            adj = feedback_adjustment(db, host)
            score, _ = compute_score(clean["components"], adj)
            fp = fingerprint_for(clean["title"])
            db.add(Lead(
                fingerprint=fp, submission_id=sub.id, source_url=sub.source_url, source_host=host,
                title=clean["title"], problem=clean["problem"], who_pays=clean["who_pays"],
                company=clean["company"], why_now=clean["why_now"],
                first_customer_route=clean["first_customer_route"],
                smallest_first_version=clean["smallest_first_version"],
                delivery_type=clean["delivery_type"],
                tatvic_fit_note=clean["tatvic_fit_note"],
                evidence_quotes_json=json.dumps(clean["evidence_quotes"]), india_quote=clean["india_quote"],
                components_json=json.dumps(clean["components"]), score=score, weight_version=WEIGHT_VERSION,
            ))
            if not db.scalar(select(IdeaLog).where(IdeaLog.fingerprint == fp)):
                db.add(IdeaLog(fingerprint=fp, title=clean["title"]))
            funnel["saved_new"] += 1
        db.add(LeadProcessed(submission_id=sub.id, outcome="lead"))
        db.commit()
    funnel["by_host"] = dict(funnel["by_host"])
    funnel["fail_reasons"] = dict(funnel["fail_reasons"])
    return funnel


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_rejected(lead: Lead) -> bool:
    return (lead.status or "").strip().casefold() == "rejected"


def is_report_eligible(lead: Lead) -> bool:
    comps = json.loads(lead.components_json)
    return (
        not is_rejected(lead)
        and lead.status == "new"
        and lead.score >= MIN_LEAD_SCORE
        and comps.get("india_relevance", 1) >= MIN_INDIA_RELEVANCE
        and lead.delivery_type in {"service", "product"}
        and bool((lead.why_now or "").strip())
        and bool((lead.first_customer_route or "").strip())
    )


def select_for_report(db: Session) -> tuple[list[Lead], list[Lead], list[Lead]]:
    """Return (new_leads, suppressed_repeats, watchlist). Does NOT mutate.

    Rejected leads stay in the database for review and never enter Top 5,
    watchlist, or the CEO email.
    """
    leads = db.scalars(select(Lead).order_by(Lead.score.desc())).all()
    fresh, repeats, watch = [], [], []
    for lead in leads:
        if is_rejected(lead) or lead.status != "new":
            continue
        if is_report_eligible(lead):
            log = db.scalar(select(IdeaLog).where(IdeaLog.fingerprint == lead.fingerprint))
            if log and log.times_surfaced > 0 and abs(lead.score - (log.last_surfaced_score or 0)) < RESURFACE_SCORE_DELTA:
                repeats.append(lead)
            else:
                fresh.append(lead)
        else:
            watch.append(lead)
    return fresh, repeats, watch


def mark_surfaced(db: Session, leads: list[Lead]) -> None:
    for lead in leads:
        log = db.scalar(select(IdeaLog).where(IdeaLog.fingerprint == lead.fingerprint))
        if log is None:
            log = IdeaLog(fingerprint=lead.fingerprint, title=lead.title)
            db.add(log)
        log.times_surfaced = (log.times_surfaced or 0) + 1
        log.last_surfaced_score = lead.score
        log.last_seen = _now()
    db.commit()


def record_feedback(db: Session, lead_id: int, decision: str, reason: str, reviewer: str = "reviewer") -> LeadFeedback:
    if decision not in {"accepted", "rejected"}:
        raise ValueError("decision must be accepted or rejected")
    if not (reason or "").strip():
        raise ValueError("A non-empty reason is required.")
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise ValueError(f"Lead {lead_id} not found")
    fb = LeadFeedback(lead_id=lead_id, decision=decision, reason=reason.strip(), reviewer=reviewer)
    lead.status = decision
    db.add(fb)
    db.commit()
    return fb


def top_rejection_reasons(db: Session, n: int = 5) -> list[tuple[str, int]]:
    reasons = db.scalars(select(LeadFeedback.reason).where(LeadFeedback.decision == "rejected")).all()
    return Counter(r.strip().casefold() for r in reasons).most_common(n)
