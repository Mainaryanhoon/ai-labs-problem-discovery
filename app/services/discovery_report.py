"""Local discovery report writer for pipeline runs (no email).

Canonical path: ``data/reports/opportunities_YYYY-MM-DD.md`` (UTC date).
Independent of Gmail/SMTP configuration.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PipelineSubmissionState, ProblemCluster, Submission
from app.tatvic_scoring import WEIGHT_VERSION

SummarizeFn = Callable[[int, Session], dict[str, Any]]

MAX_RANKED = 10
TARGET_MIN = 5


def discovery_report_path(
    *,
    run_date: date | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    settings = get_settings()
    root = Path(output_dir or settings.report_output_dir)
    day = (run_date or datetime.now(timezone.utc).date()).isoformat()
    return root / f"opportunities_{day}.md"


def partition_discovery_items(
    summaries: list[dict[str, Any]],
    *,
    max_ranked: int = MAX_RANKED,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split summaries into product-ready rows and a watchlist.

    Backward-compatible return: ``(validated_problem_opportunities, watchlist)``.
    Commercial leads (company-qualified) are a subset handled in the report
    renderer via ``partition_commercial_discovery``.
    """
    commercial, validated, watchlist = partition_commercial_discovery(
        summaries,
        max_ranked=max_ranked,
    )
    # Historical callers treat the first list as ranked product-ready rows.
    ranked = [*commercial, *validated][:max_ranked]
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked, watchlist


def partition_commercial_discovery(
    summaries: list[dict[str, Any]],
    *,
    max_ranked: int = MAX_RANKED,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (commercial_leads, validated_problem_opportunities, watchlist)."""
    product_ready = [
        item
        for item in summaries
        if item.get("product_ready") is True
        and isinstance(item.get("score"), (int, float))
        and not isinstance(item.get("score"), bool)
    ]
    product_ready.sort(
        key=lambda item: (-float(item["score"]), int(item["cluster_id"]))
    )

    commercial: list[dict[str, Any]] = []
    validated: list[dict[str, Any]] = []
    for item in product_ready:
        qual = item.get("commercial_qualification") or {}
        if qual.get("is_commercial_lead") is True:
            commercial.append(item)
        else:
            validated.append(item)
    commercial = commercial[:max_ranked]
    validated = validated[: max(0, max_ranked - len(commercial))]
    for index, item in enumerate(commercial, start=1):
        item["rank"] = index
    for index, item in enumerate(validated, start=1):
        item["rank"] = index

    listed_ids = {item["cluster_id"] for item in commercial + validated}
    watchlist: list[dict[str, Any]] = []
    for item in summaries:
        if item["cluster_id"] in listed_ids:
            continue
        metrics = item.get("demand_metrics") or {}
        keep_demand = int(metrics.get("keep_demand_count") or 0)
        demand_role = int(metrics.get("demand_role_item_count") or keep_demand)
        eligible = int(item.get("eligible_evidence_count") or 0)
        if (
            item.get("top10_eligible") is True
            or demand_role > 0
            or keep_demand > 0
            or eligible > 0
        ):
            watchlist.append(item)
    watchlist.sort(
        key=lambda item: (
            0 if item.get("top10_eligible") else 1,
            -float(item["score"] or 0),
            int(item["cluster_id"]),
        )
    )
    return commercial, validated, watchlist


def _reason_code_histogram(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = db.scalars(
        select(Submission).where(Submission.analysis_status == "completed")
    ).all()
    for submission in rows:
        if not submission.analysis_json:
            continue
        try:
            import json

            payload = json.loads(submission.analysis_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        code = payload.get("filter_reason_code")
        if isinstance(code, str) and code.strip():
            key = code.strip()
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _review_queue_count(db: Session) -> int:
    needs_review = db.scalar(
        select(func.count())
        .select_from(PipelineSubmissionState)
        .where(PipelineSubmissionState.clustering_status == "needs_review")
    )
    return int(needs_review or 0)


def _format_source_yield_line(source: str, outcome: dict[str, Any]) -> str:
    status = outcome.get("status", "—")
    fetch_status = outcome.get("fetch_status", "—")
    parts = [
        f"`{source}`: status={status}",
        f"fetch_status={fetch_status}",
    ]
    for key in (
        "fetched",
        "created",
        "new",
        "rejected",
        "duplicates_skipped",
        "irrelevant_skipped",
        "budget_skipped",
        "outside_budget",
        "feed_failures",
        "page_fetch_failures",
        "bounded_skipped",
        "queries_attempted",
        "error_code",
    ):
        if key in outcome and outcome[key] is not None:
            parts.append(f"{key}={outcome[key]}")
    note = outcome.get("message") or outcome.get("note")
    if isinstance(note, str) and note.strip():
        # Keep operator notes short and secret-safe (collectors already are).
        parts.append(f"note={note.strip()[:160]}")
    feed_statuses = outcome.get("feed_statuses")
    if isinstance(feed_statuses, list):
        parts.extend(
            f"feed={item['url']}:{item['status']}"
            for item in feed_statuses
            if isinstance(item, dict)
            and isinstance(item.get("url"), str)
            and isinstance(item.get("status"), str)
        )
    return "- " + ", ".join(parts)


def _nonempty_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and value.strip() != "—":
        return value.strip()
    return None


def _delivery_label(item: dict[str, Any]) -> str:
    raw = (item.get("delivery_type") or "").strip().casefold()
    if raw == "service":
        return "service"
    if raw == "product":
        return "standalone product"
    return "unclear (service vs standalone product)"


def _why_now_text(item: dict[str, Any]) -> str:
    return (
        _nonempty_text(item.get("why_now"))
        or "Why now is not evidenced in stored analysis (no dated trigger)."
    )


def _reach_strategy_text(item: dict[str, Any]) -> str:
    return (
        _nonempty_text(item.get("first_customer_reach_strategy"))
        or "First-customer acquisition strategy is not evidenced "
        "(a job title is not an outreach route)."
    )


def _lead_executive_summary(item: dict[str, Any]) -> list[str]:
    problem = item.get("title") or "Untitled"
    who = (
        _nonempty_text(item.get("first_client_type"))
        or "buyer not named"
    )
    return [
        f"{problem}: {who} may pay; delivery is {_delivery_label(item)}.",
        f"Why now: {_why_now_text(item)}",
        f"First customer acquisition: {_reach_strategy_text(item)}",
    ]


def _report_executive_summary(
    *,
    commercial_leads: list[dict[str, Any]],
    validated_problems: list[dict[str, Any]],
    watchlist: list[dict[str, Any]],
) -> list[str]:
    leads = [*commercial_leads, *validated_problems]
    if leads:
        top = leads[0]
        line1 = (
            f"{len(leads)} weekly business lead(s); top: "
            f"\"{top.get('title') or 'Untitled'}\" "
            f"({top.get('score')}/100, {_delivery_label(top)})."
        )
    else:
        line1 = "No weekly business leads this run; nothing was padded."
    line2 = (
        f"{len(commercial_leads)} company-level commercial row(s); "
        f"{len(watchlist)} on the watchlist."
    )
    line3 = (
        "These are leads from public sources, not validated demand; "
        "accept or reject each one with a reason."
    )
    return [line1, line2, line3]


def _format_opportunity_block(item: dict[str, Any]) -> list[str]:
    metrics = item.get("demand_metrics") or {}
    qual = item.get("commercial_qualification") or {}
    v02 = item.get("commercial_score_v02") or {}
    who = (
        item.get("first_client_type")
        or "See evidence / first client type TBD"
    )
    problem = item.get("title") or "Untitled"
    lines = [
        f"### #{item.get('rank')} — {problem}",
        *_lead_executive_summary(item),
        "",
        f"- Section / vertical: {item.get('section') or '—'} / "
        f"{item.get('category') or '—'}",
        f"- Problem: {problem}",
        f"- Who has the problem: {who}",
        f"- Why now: {_why_now_text(item)}",
        f"- First customer acquisition strategy: {_reach_strategy_text(item)}",
        f"- Service vs standalone product: {_delivery_label(item)}",
        f"- Evidence level (demand-role platforms): "
        f"{item.get('evidence_level')}",
        f"- Demand-role counts (code): "
        f"items={metrics.get('demand_role_item_count', metrics.get('item_count', '—'))}, "
        f"authors={metrics.get('distinct_authors', '—')}, "
        f"demand_platforms={metrics.get('distinct_source_platforms', '—')}, "
        f"KEEP_DEMAND_on_demand_sources="
        f"{metrics.get('keep_demand_count', '—')}, "
        f"supporting_capability="
        f"{metrics.get('supporting_capability_item_count', item.get('supporting_capability_evidence_count', 0))}",
        f"- Active score (v0.1): {item.get('score')} / 100 "
        f"(weight_version=`{item.get('weight_version') or WEIGHT_VERSION}`)",
        f"- Commercial score (v0.2-commercial, inactive): "
        f"{v02.get('score', '—')} / 100 "
        f"(active_for_ranking={v02.get('active_for_ranking', False)})",
        f"- India relevance: {qual.get('india_relevance', 'none')}",
        f"- Company-level lead: "
        f"{'yes' if qual.get('is_commercial_lead') else 'no'} "
        f"(company={qual.get('company_name') or 'unavailable'})",
        f"- Company identity: "
        f"{qual.get('company_identity_resolution') or 'unresolved / unavailable'}",
        f"- Offering angle: {item.get('offering_angle')}",
        f"- Smallest first version: "
        f"{item.get('smallest_first_version') or '—'}",
        f"- First client type: {item.get('first_client_type') or '—'}",
        f"- Why it might fail: {item.get('failure_risk_line') or '—'}",
        f"- Cluster id: {item.get('cluster_id')}",
    ]
    gates = qual.get("qualification_gates") or {}
    if isinstance(gates, dict) and gates:
        lines.append("- Commercial qualification gates:")
        for gate_key in (
            "company_trigger",
            "india_presence",
            "buying_evidence",
            "company_identity_resolution",
            "company_specific_demand",
            "verified_buyer_route",
        ):
            gate_info = gates.get(gate_key)
            if not isinstance(gate_info, dict):
                continue
            status = gate_info.get("status", "FAILED")
            reason = gate_info.get("reason", "")
            uncertainty = gate_info.get("uncertainty")
            unc_label = f" (uncertainty: {uncertainty})" if uncertainty else ""
            lines.append(f"  - {gate_key}: **{status}** — {reason}{unc_label}")
            citations = gate_info.get("citations") or []
            for cit in citations[:2]:
                url = cit.get("source_url") or "URL unavailable"
                pub = cit.get("publication_date") or "unknown date"
                ev_date = cit.get("event_date") or cit.get("event_date_quote")
                ev_str = f"; event_date={ev_date}" if ev_date else ""
                quote = cit.get("evidence_quote") or ""
                quote_str = f': "{quote[:100]}"' if quote else ""
                lines.append(f"    - Citation: [{url}] (pub={pub}{ev_str}){quote_str}")
    for key, label in (
        ("demand_evidence", "Practitioner demand evidence"),
        ("capability_context_evidence", "Capability / context evidence"),
    ):
        evidence_rows = item.get(key) or []
        if not evidence_rows:
            continue
        lines.append(f"- {label}:")
        for evidence in evidence_rows[:3]:
            lines.append(
                f"  - {evidence.get('source_name') or 'source'}, "
                f"date={evidence.get('source_date') or 'unknown'}: "
                f"\"{evidence.get('evidence_quote')}\" "
                f"[{evidence.get('source_url') or 'URL unavailable'}]"
            )
            reason = evidence.get("classification_reason")
            if isinstance(reason, str) and reason.strip():
                lines.append(f"    Reason: {reason.strip()}")
    lines.extend([
        f"- Unknowns to validate: "
        + (
            "; ".join(qual.get("unknowns_to_validate") or [])
            or "—"
        ),
        "",
        "Reviewer Actions",
        "- [ ] Accepted - Reason: ____",
        "- [ ] Rejected - Reason: ____",
        "",
    ])
    return lines


def render_discovery_opportunities_markdown(
    *,
    run_date: date,
    ranked: list[dict[str, Any]],
    watchlist: list[dict[str, Any]],
    run_id: str | None = None,
    collection_summary: dict[str, Any] | None = None,
    stage_counts: dict[str, Any] | None = None,
    reason_codes: dict[str, int] | None = None,
    review_queue_count: int = 0,
    weight_version: str = WEIGHT_VERSION,
    commercial_leads: list[dict[str, Any]] | None = None,
    validated_problems: list[dict[str, Any]] | None = None,
    previous_run_comparison: str | None = None,
    company_trigger_watchlist: list[dict[str, Any]] | None = None,
) -> str:
    if commercial_leads is None or validated_problems is None:
        commercial_leads, validated_problems, _ = partition_commercial_discovery(
            [*ranked, *watchlist]
        )
        # Prefer explicit ranked list as validated when partition inputs are thin.
        if not commercial_leads and not validated_problems and ranked:
            validated_problems = ranked

    lines = [
        f"# Tatvic opportunities — {run_date.isoformat()}",
        "",
        *_report_executive_summary(
            commercial_leads=commercial_leads,
            validated_problems=validated_problems,
            watchlist=watchlist,
        ),
        "",
        f"- Run date (UTC): `{run_date.isoformat()}`",
        f"- Active weight version: `{weight_version}` "
        "(commercial `v0.2-commercial` is informational only)",
        f"- Status: draft for Khilav review",
    ]
    if run_id:
        lines.append(f"- Pipeline run id: `{run_id}`")
    lines.append(
        f"- Commercial leads: {len(commercial_leads)} "
        f"| Weekly Business Leads: {len(validated_problems)} "
        f"(target {TARGET_MIN}–{MAX_RANKED} combined; never padded)"
    )
    lines.append(f"- Review queue (needs_review clustering): {review_queue_count}")
    lines.append("")

    lines.append("## D. Source and evidence health")
    lines.append("")
    if collection_summary:
        lines.append(
            "Statuses distinguish `not_requested`, successful zero-result "
            "fetches, and failed fetches. Demand floor uses demand-role "
            "sources only (SOURCES.md); HN/docs/articles do not inflate it."
        )
        lines.append("")
        for source, outcome in collection_summary.items():
            if not isinstance(outcome, dict):
                continue
            lines.append(_format_source_yield_line(source, outcome))
        lines.append("")
    else:
        lines.append("No per-source collection summary for this write.")
        lines.append("")

    if isinstance(stage_counts, dict) and stage_counts:
        lines.append("### Pipeline funnel (this run)")
        lines.append("")
        analysis = stage_counts.get("analysis") or {}
        clustering = stage_counts.get("clustering") or {}
        if isinstance(analysis, dict) and analysis:
            lines.append(
                "- Analysis: "
                + ", ".join(
                    f"{key}={analysis[key]}"
                    for key in (
                        "processed",
                        "completed",
                        "failed",
                        "skipped_completed",
                        "deferred",
                        "provider_not_configured",
                        "rate_limited",
                    )
                    if key in analysis
                )
            )
        if isinstance(clustering, dict) and clustering:
            lines.append(
                "- Clustering: "
                + ", ".join(
                    f"{key}={clustering[key]}"
                    for key in (
                        "considered",
                        "matched",
                        "created",
                        "duplicates",
                        "already_clustered",
                        "needs_review",
                        "failed",
                        "skipped",
                        "deferred_clustering_count",
                    )
                    if key in clustering
                )
            )
        lines.append("")

    if reason_codes:
        lines.append("### Items per reason code")
        lines.append("")
        for code, count in reason_codes.items():
            lines.append(f"- `{code}`: {count}")
        lines.append("")

    lines.append("## Changes since previous comparable run")
    lines.append("")
    lines.append(
        previous_run_comparison
        or "Comparison unavailable: no comparable prior discovery snapshot "
        "was linked for this write."
    )
    lines.append("")

    lines.append("## A. Commercial leads")
    lines.append("")
    if not commercial_leads:
        lines.append(
            "No company-level commercial leads qualified. Approved sources "
            "did not yield named companies, dated triggers, and verified "
            "buying evidence together. Nothing was padded."
        )
        lines.append("")
    else:
        for item in commercial_leads:
            lines.extend(_format_opportunity_block(item))

    lines.append("## B. Weekly Business Leads")
    lines.append("")
    if not validated_problems:
        lines.append(
            "No weekly business leads met the demand-role "
            "evidence floor (demand-role KEEP_DEMAND, ≥3 independent items, "
            "≥2 authors, in-scope section, clear offering angle)."
        )
        lines.append("")
    else:
        combined = len(commercial_leads) + len(validated_problems)
        if combined < TARGET_MIN:
            lines.append(
                f"**Shortfall:** evidence supports only **{combined}** "
                f"qualified row(s) (target at least {TARGET_MIN}). "
                "See the watchlist for emerging clusters; do not pad."
            )
            lines.append("")
        for item in validated_problems:
            lines.extend(_format_opportunity_block(item))

    lines.append("## C. Emerging signals / watchlist")
    lines.append("")
    if not watchlist:
        lines.append(
            "No emerging clusters beyond the listed sets "
            "(or none with demand-role / eligibility signals)."
        )
        lines.append("")
    else:
        for item in watchlist:
            metrics = item.get("demand_metrics") or {}
            blockers = [
                line
                for line in (item.get("limitations") or [])
                if "product-ready" in line.casefold()
                or "top 10" in line.casefold()
                or "keep_demand" in line.casefold()
                or "offering" in line.casefold()
                or "demand" in line.casefold()
            ][:3]
            why = "; ".join(blockers) if blockers else "Not product-ready yet"
            lines.extend([
                f"- Cluster {item.get('cluster_id')}: {item.get('title')} "
                f"(product_ready={item.get('product_ready')}, "
                f"demand_role_items="
                f"{metrics.get('demand_role_item_count', metrics.get('item_count'))}, "
                f"supporting_capability="
                f"{metrics.get('supporting_capability_item_count', item.get('supporting_capability_evidence_count', 0))}, "
                f"score={item.get('score')}) — {why}",
            ])
        lines.append("")

    lines.append("### Company triggers to investigate (not ranked demand)")
    lines.append("")
    lines.append(
        "A trigger identifies a timely account to verify; it does not establish "
        "technical pain, buying intent, or buyer identity."
    )
    if not company_trigger_watchlist:
        lines.append("No policy-authorized company trigger claims are available.")
    else:
        for account in company_trigger_watchlist:
            lines.append(
                f"- {account['company_name']} (identity={account.get('identity_resolution', 'unresolved')})"
            )
            if account.get("identity_resolution_reason"):
                lines.append(
                    f"  - Identity note: {account['identity_resolution_reason']}"
                )
            for trigger in account.get("company_triggers", []):
                lines.append(
                    "  - Trigger: "
                    f"{trigger.get('trigger_type') or 'uncertain'}; "
                    f"publication={trigger.get('publication_date') or 'unknown'}; "
                    f"event_date={trigger.get('event_date') or 'unparsed'} "
                    f"(evidence={trigger.get('event_date_quote') or 'unverified'}); "
                    f"status={trigger.get('status') or 'unknown'}"
                )
                lines.append(
                    f"  - Evidence: \"{trigger.get('evidence_quote')}\" "
                    f"[{trigger.get('source_url')}]"
                )
            for role_key, label in (
                ("india_evidence", "India evidence"),
                ("buying_evidence", "Buying evidence"),
                ("buyer_routes", "Verified public route"),
            ):
                for evidence in account.get(role_key, []):
                    lines.append(
                        f"  - {label} ({evidence.get('status')}): "
                        f"\"{evidence.get('evidence_quote')}\" "
                        f"[{evidence.get('source_url')}]"
                    )
    lines.append("")

    return "\n".join(lines)


def write_discovery_opportunities_report(
    db: Session,
    summarize: SummarizeFn,
    *,
    run_id: str | None = None,
    run_date: date | None = None,
    collection_summary: dict[str, Any] | None = None,
    stage_counts: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Write/overwrite the canonical daily discovery markdown report."""
    day = run_date or datetime.now(timezone.utc).date()
    path = discovery_report_path(run_date=day, output_dir=output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    cluster_ids = db.scalars(
        select(ProblemCluster.id).order_by(ProblemCluster.id.asc())
    ).all()
    summaries: list[dict[str, Any]] = []
    for cluster_id in cluster_ids:
        try:
            summaries.append(summarize(cluster_id, db))
        except Exception:
            continue

    commercial, validated, watchlist = partition_commercial_discovery(summaries)
    ranked = [*commercial, *validated]
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    from app.commercial_evidence import build_company_trigger_watchlist

    company_trigger_watchlist = build_company_trigger_watchlist(db)
    markdown = render_discovery_opportunities_markdown(
        run_date=day,
        ranked=ranked,
        watchlist=watchlist,
        run_id=run_id,
        collection_summary=collection_summary,
        stage_counts=stage_counts,
        reason_codes=_reason_code_histogram(db),
        review_queue_count=_review_queue_count(db),
        commercial_leads=commercial,
        validated_problems=validated,
        previous_run_comparison=_previous_run_comparison(db, run_id),
        company_trigger_watchlist=company_trigger_watchlist,
    )
    path.write_text(markdown, encoding="utf-8")
    return {
        "status": "written",
        "path": str(path),
        "run_date": day.isoformat(),
        "ranked_count": len(ranked),
        "commercial_lead_count": len(commercial),
        "validated_problem_count": len(validated),
        "watchlist_count": len(watchlist),
        "shortfall": len(ranked) < TARGET_MIN,
    }


def _previous_run_comparison(db: Session, run_id: str | None) -> str:
    """Honest run-over-run note; no invented deltas."""
    from app.models import PipelineRun

    if not run_id:
        return (
            "Comparison unavailable: this report write has no pipeline run id."
        )
    runs = db.scalars(
        select(PipelineRun).order_by(PipelineRun.started_at.desc())
    ).all()
    current = next((run for run in runs if run.id == run_id), None)
    prior = None
    for run in runs:
        if run.id == run_id:
            continue
        if run.status in {"completed", "partial"}:
            prior = run
            break
    if current is None or prior is None:
        return (
            "Comparison unavailable: no prior completed/partial pipeline run "
            "exists to compare evidence and qualification status."
        )
    return (
        f"Prior comparable run `{prior.id}` "
        f"(status={prior.status}, started={prior.started_at}). "
        "Opportunity-level evidence/qualification deltas are not yet "
        "persisted as a dedicated snapshot; inspect stage_counts on both "
        "runs for collection/analysis/clustering differences. Do not treat "
        "rank movement alone as commercial progress."
    )
