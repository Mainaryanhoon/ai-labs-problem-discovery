"""Tatvic buying-filter scoring and offering-angle rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

WEIGHT_VERSION = "v0.1"

COMPONENT_WEIGHTS: dict[str, int] = {
    "commercial_signal": 35,
    "demand_strength": 20,
    "india_relevance": 15,
    "section_client_fit": 10,
    "capability_readiness": 10,
    "effort_to_first_version": 5,
    "defensibility": 5,
}
assert sum(COMPONENT_WEIGHTS.values()) == 100

TatvicSection = Literal[
    "Analytics",
    "Cloud and Data Engineering",
    "Data Science and AI/ML",
    "Data Science and AI",  # legacy alias accepted in stored JSON
    "Agentic AI",
    "CRO",
    "OUT_OF_SCOPE",
]

EvidenceLevelLabel = Literal["strong", "moderate", "thin", "insufficient"]

OfferingAngle = Literal[
    "upgrade_existing_offering",
    "new_agent_or_offering",
    "cheaper_delivery",
    "undetermined",
]

# ASSETS.md offerings — leverage 4+ / product-ready rows must cite one of these.
KNOWN_OFFERING_MARKERS: tuple[str, ...] = (
    "bq agent",
    "bigquery agent",
    "cro excalibur",
    "excalibur",
    "timesfm",
    "agentic timesfm",
    "tag auditor",
    "ga tag auditor",
    "anomaly detection agent",
    "ga4",
    "tag manager",
    "gtm",
    "measurement",
    "cro program",
)

# Problems that are never Tatvic product candidates (code gate).
NON_PRODUCT_PATTERNS = (
    r"\bexcel\b",
    r"\bactivesheet\b",
    r"\bvba\b",
    r"\bworksheet\b",
    r"\bpostgres\.?js\b",
    r"\bfrontend hang",
    r"\breact\b.*\bhang",
    r"\bchrome pdf\b",
    r"\bpdf viewer\b",
    r"\bflink\b",
    r"\bkafka\b",
    r"\btruststore\b",
)

KEEP_CODES_FOR_DEMAND = frozenset({"KEEP_DEMAND"})
KEEP_CODES_FOR_MARKET_LEAD = frozenset({"KEEP_DEMAND", "N1_IDEA_CONTEXT"})

_BUDGET_HOLDER_RE = re.compile(
    r"\b("
    r"founder|co-?founder|owner|"
    r"cmo|ceo|cfo|cto|coo|"
    r"vp|vice[-\s]?president|"
    r"director|budget[-\s]?holder"
    r")\b",
    re.IGNORECASE,
)

POLICY_DIR = Path(__file__).resolve().parent.parent / "docs" / "tatvic"


def cites_known_offering(*texts: str | None) -> bool:
    blob = " ".join(t for t in texts if isinstance(t, str)).casefold()
    return any(marker in blob for marker in KNOWN_OFFERING_MARKERS)


def is_non_product_problem(*texts: str | None) -> bool:
    blob = " ".join(t for t in texts if isinstance(t, str)).casefold()
    return any(re.search(pattern, blob) for pattern in NON_PRODUCT_PATTERNS)


def indicates_budget_holder(first_client_type: str | None) -> bool:
    if not isinstance(first_client_type, str) or not first_client_type.strip():
        return False
    return bool(_BUDGET_HOLDER_RE.search(first_client_type))


def has_commercial_bypass(
    components: dict[str, int],
    first_client_type: str | None,
) -> bool:
    """High commercial signal or a budget-holder buyer can leave Tatvic-offering gates."""
    commercial = components.get("commercial_signal", 1)
    try:
        commercial_score = int(commercial)
    except (TypeError, ValueError):
        commercial_score = 1
    return commercial_score >= 4 or indicates_budget_holder(first_client_type)


def is_product_ready(
    *,
    top10_eligible: bool,
    offering_angle: OfferingAngle,
    components: dict[str, int],
    smallest_first_version: str | None,
    first_client_type: str | None,
    failure_risk_line: str | None,
    keep_demand_count: int,
    title: str | None = None,
    problem_texts: list[str] | None = None,
    in_scope_section: bool = False,
) -> tuple[bool, list[str]]:
    """
    ASSETS.md gate: only surface opportunities that could become a Tatvic
    agent/service upgrade (BQ Agent / Excalibur / TimesFM pattern).
    """
    blockers: list[str] = []
    texts = [title or "", *(problem_texts or [])]
    commercial_bypass = has_commercial_bypass(components, first_client_type)
    if is_non_product_problem(*texts) and not commercial_bypass:
        blockers.append(
            "Problem matches non-product patterns (Excel/VBA, generic frontend "
            "or infra tooling) outside Tatvic delivery domains."
        )
    if keep_demand_count < 1:
        blockers.append("No KEEP_DEMAND evidence in the cluster.")
    if not top10_eligible:
        blockers.append(
            "Needs ≥3 independent items, ≥2 authors, and an in-scope section."
        )
    if offering_angle == "undetermined":
        blockers.append("No clear offering angle under RUBRIC §6.7.")
    leverage = components.get("leverage_on_offerings", 1)
    if offering_angle == "upgrade_existing_offering" and leverage < 4:
        blockers.append("Upgrade angle requires leverage ≥ 4.")
    if offering_angle == "new_agent_or_offering":
        if components.get("demand_strength", 1) < 3:
            blockers.append("New offering angle requires demand ≥ 3.")
        if (
            components.get("capability_readiness", 1) < 3
            and not commercial_bypass
        ):
            blockers.append("New offering angle requires readiness ≥ 3.")
    if leverage < 3 and offering_angle != "cheaper_delivery":
        blockers.append(
            "Leverage < 3: does not reuse Tatvic offerings enough to productize."
        )
    _offering_cited = cites_known_offering(
        smallest_first_version,
        first_client_type,
        failure_risk_line,
        title,
        *(problem_texts or []),
    )
    # General known-offering gate: only block when the cluster is NOT in an
    # in-scope section. In-scope sections (Analytics, Cloud and Data Engineering,
    # etc.) are sufficient to satisfy the keyword gate for non-upgrade angles.
    if not _offering_cited and not in_scope_section and not commercial_bypass:
        blockers.append(
            "Does not cite a known Tatvic offering (BQ Agent, CRO Excalibur, "
            "Agentic TimesFM, Tag Auditor, GA4/GTM, CRO programmes)."
        )
    # Upgrade angle ALWAYS requires a cited known offering (Req 3.7), regardless
    # of in_scope_section — upgrade is only valid against a named existing offering.
    if offering_angle == "upgrade_existing_offering" and not _offering_cited:
        blockers.append("Upgrade angle requires a cited known offering.")
    for label, value in (
        ("smallest first version", smallest_first_version),
        ("first client type", first_client_type),
        ("failure risk", failure_risk_line),
    ):
        if not isinstance(value, str) or not value.strip():
            blockers.append(f"Missing {label} (required for Top opportunities).")
        elif value.strip().casefold().startswith("n/a"):
            blockers.append(f"{label} is N/A — item was dropped, not productizable.")
    return (len(blockers) == 0, blockers)


# OpenClaw six-file brain — these are the only policy docs the classifier must obey.
OPENCLAW_POLICY_FILES: dict[str, str] = {
    "soul": "SOUL.md",
    "goal": "GOAL.md",
    "rubric": "RUBRIC.md",
    "assets": "ASSETS.md",
    "sources": "SOURCES.md",
    "agents": "AGENTS.md",
}


def load_policy_context() -> dict[str, str]:
    """Load the six OpenClaw policy files that drive keep/drop and ranking."""
    context: dict[str, str] = {}
    for key, name in OPENCLAW_POLICY_FILES.items():
        path = POLICY_DIR / name
        try:
            context[key] = path.read_text(encoding="utf-8")
        except OSError:
            context[key] = ""
    return context


def policy_files_loaded() -> list[str]:
    """Return names of policy files that are present on disk."""
    return [
        name
        for name in OPENCLAW_POLICY_FILES.values()
        if (POLICY_DIR / name).is_file()
    ]


def _bounded_component(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= 5:
        return None
    return value


def compute_demand_strength_1_to_5(
    *,
    item_count: int,
    distinct_source_platforms: int,
    month_over_month_rising: bool,
    has_previous_window_data: bool = True,
) -> int:
    """
    Demand strength from counted evidence only (never from the model).

    RUBRIC §6.4 — use the highest score any rule matches. If there is no
    previous-window data, rising is false and demand cannot exceed 4.
    """
    rising = bool(month_over_month_rising and has_previous_window_data)
    score = 1
    if item_count <= 0:
        return 1
    if item_count == 1:
        score = 1
    if 2 <= item_count <= 3:
        score = max(score, 2)
    if 4 <= item_count <= 9 or distinct_source_platforms >= 2:
        score = max(score, 3)
    if 10 <= item_count <= 24 or (
        4 <= item_count <= 9 and distinct_source_platforms >= 3
    ):
        score = max(score, 4)
    if item_count >= 25 and distinct_source_platforms >= 3:
        score = max(score, 4)
        if rising:
            score = max(score, 5)
    if not has_previous_window_data:
        score = min(score, 4)
    return score


def evidence_level_from_platforms(platform_count: int) -> EvidenceLevelLabel:
    if platform_count >= 3:
        return "strong"
    if platform_count == 2:
        return "moderate"
    if platform_count == 1:
        return "thin"
    return "insufficient"


def _median_component(
    records: list[dict[str, Any]],
    key: str,
    *,
    fallback: int | None = None,
) -> int | None:
    values = [
        v
        for item in records
        if (v := _bounded_component(item.get(key))) is not None
    ]
    if not values:
        return fallback
    values.sort()
    return values[(len(values) - 1) // 2]


def commercial_from_legacy_willingness(signal: str | None) -> int:
    if signal == "explicit":
        return 5
    if signal == "suggested":
        return 3
    if signal == "none":
        return 1
    return 2


def _cluster_india_blob(
    *,
    eligible_analyses: list[dict[str, Any]],
    submissions: list[Any],
    cluster_title: str | None,
) -> str:
    parts: list[str] = []
    if cluster_title:
        parts.append(cluster_title)
    for item in eligible_analyses:
        for key in (
            "problem_statement",
            "context",
            "affected_user",
            "first_client_type",
            "india_quote",
            "smallest_first_version",
        ):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
    for submission in submissions:
        text = getattr(submission, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n".join(parts)


def india_relevance_from_cluster(
    *,
    eligible_analyses: list[dict[str, Any]],
    submissions: list[Any],
    cluster_title: str | None,
) -> tuple[int, int, bool]:
    """Return (score, text_cap, missing_evidence).

    Model-claimed ``india_relevance_1_to_5`` cannot exceed the leads-lane
    text cap. With no claim, the cap itself is the score.
    """
    from app.leads.scoring import india_cap_from_text

    blob = _cluster_india_blob(
        eligible_analyses=eligible_analyses,
        submissions=submissions,
        cluster_title=cluster_title,
    )
    cap = india_cap_from_text(blob)
    claimed = _median_component(eligible_analyses, "india_relevance_1_to_5")
    if claimed is None:
        return cap, cap, cap <= 1
    return min(claimed, cap), cap, cap <= 1


@dataclass(frozen=True)
class TatvicOpportunityScore:
    score: float | None
    weight_version: str
    evidence_level: EvidenceLevelLabel
    components: dict[str, int]
    component_contributions: dict[str, float]
    reasons: list[str]
    limitations: list[str]
    top10_eligible: bool
    product_ready: bool
    single_source_capped: bool
    offering_angle: OfferingAngle
    smallest_first_version: str | None
    first_client_type: str | None
    failure_risk_line: str | None
    section: str | None
    demand_metrics: dict[str, int | bool]


def score_tatvic_opportunity(
    *,
    eligible_analyses: list[dict[str, Any]],
    submissions: list[Any],
    source_platforms: set[str],
    item_count: int,
    distinct_authors: int,
    records_last_30d: int,
    records_prior_30d: int,
    primary_section: str | None,
    cluster_title: str | None = None,
) -> TatvicOpportunityScore:
    """Score a cluster using Tatvic buying filters."""
    limitations: list[str] = []
    reasons: list[str] = []

    has_previous_window_data = records_prior_30d > 0
    month_over_month_rising = records_last_30d > records_prior_30d
    demand = compute_demand_strength_1_to_5(
        item_count=item_count,
        distinct_source_platforms=len(source_platforms),
        month_over_month_rising=month_over_month_rising,
        has_previous_window_data=has_previous_window_data,
    )
    reasons.append(
        f"Demand strength {demand}/5 from {item_count} items, "
        f"{distinct_authors} authors, {len(source_platforms)} source "
        f"platform(s); 30d trend "
        f"{'up' if month_over_month_rising else 'flat or down'}."
    )

    readiness = _median_component(
        eligible_analyses, "capability_readiness_1_to_5"
    )
    fit = _median_component(eligible_analyses, "section_client_fit_1_to_5")
    leverage = _median_component(
        eligible_analyses, "leverage_on_offerings_1_to_5"
    )
    commercial = _median_component(
        eligible_analyses, "commercial_signal_1_to_5"
    )
    effort = _median_component(
        eligible_analyses, "effort_to_first_version_1_to_5"
    )
    defensibility = _median_component(
        eligible_analyses, "defensibility_1_to_5"
    )

    # Legacy willingness may fill commercial only when it carries an explicit
    # ladder value. Unclear/missing willingness is not evidence (RUBRIC §6.5).
    if commercial is None and eligible_analyses:
        willingness = eligible_analyses[0].get("willingness_to_pay_signal")
        if willingness in {"explicit", "suggested", "none"}:
            commercial = commercial_from_legacy_willingness(willingness)
            limitations.append(
                "Commercial signal inferred from legacy willingness_to_pay "
                "until tatvic commercial_signal_1_to_5 is populated."
            )

    india, india_cap, india_missing = india_relevance_from_cluster(
        eligible_analyses=eligible_analyses,
        submissions=submissions,
        cluster_title=cluster_title,
    )

    # MASTER / RUBRIC §6.5: missing qualitative evidence → score 1 + "no evidence".
    # Effort uses the same rule as the other model-owned components.
    # leverage_on_offerings remains a product-ready gate, not a ranking weight.
    qualitative = {
        "capability_readiness": readiness,
        "section_client_fit": fit,
        "leverage_on_offerings": leverage,
        "commercial_signal": commercial,
        "effort_to_first_version": effort,
        "defensibility": defensibility,
        "india_relevance": india,
    }
    components: dict[str, int] = {"demand_strength": demand}
    contributions: dict[str, float] = {}
    for name, value in qualitative.items():
        missing = value is None or (name == "india_relevance" and india_missing)
        if value is None:
            value = 1
        if missing:
            limitations.append(f"{name}: no evidence")
            reasons.append(f"{name} scored 1/5 (no evidence).")
        components[name] = value

    # RUBRIC §6.2: weight × (component − 1) ÷ 4 → range exactly 0–100.
    total = 0.0
    for key, weight in COMPONENT_WEIGHTS.items():
        part = weight * (components[key] - 1) / 4
        contributions[key] = round(part, 2)
        total += part
    score = round(total, 1)

    reasons.append(
        f"Commercial signal {components['commercial_signal']}/5 contributes "
        f"{contributions['commercial_signal']} of 100 "
        f"(weight {COMPONENT_WEIGHTS['commercial_signal']}); "
        f"capability readiness {components['capability_readiness']}/5 contributes "
        f"{contributions['capability_readiness']} "
        f"(weight {COMPONENT_WEIGHTS['capability_readiness']})."
    )
    reasons.append(
        f"India relevance {components['india_relevance']}/5 from stored text "
        f"(cap {india_cap}/5, weight {COMPONENT_WEIGHTS['india_relevance']})."
    )
    if components["commercial_signal"] >= 4:
        reasons.append(
            "Commercial demand outweighs buildability under commercial-first v0.1 weights; "
            "low capability readiness does not suppress the commercial contribution."
        )
        if components["capability_readiness"] <= 2:
            limitations.append(
                "Capability readiness is low; it is weighted at 10 and does not "
                "cancel a high commercial_signal (weight 35)."
            )

    platform_count = len(source_platforms)
    evidence_level = evidence_level_from_platforms(platform_count)
    single_source_capped = platform_count <= 1 and platform_count > 0
    if single_source_capped and score > 60:
        score = 60.0
        reasons.append(
            "Score capped at 60 because evidence comes from only one "
            "source platform."
        )

    in_scope_section = (
        primary_section is not None
        and primary_section != "OUT_OF_SCOPE"
    )
    top10_eligible = (
        item_count >= 3
        and distinct_authors >= 2
        and in_scope_section
    )
    if not top10_eligible:
        limitations.append(
            "Not Top 10 eligible: needs ≥3 items, ≥2 authors, and an "
            "in-scope Tatvic section."
        )

    offering_angle, smallest_v1, client_type, failure_line = (
        _offering_angle(components, eligible_analyses, in_scope_section)
    )

    keep_demand_count = sum(
        1
        for item in eligible_analyses
        if item.get("filter_reason_code") in KEEP_CODES_FOR_MARKET_LEAD
    )
    problem_texts = [
        str(item.get("problem_statement") or "")
        for item in eligible_analyses
    ]
    product_ready, product_blockers = is_product_ready(
        top10_eligible=top10_eligible,
        offering_angle=offering_angle,
        components=components,
        smallest_first_version=smallest_v1,
        first_client_type=client_type,
        failure_risk_line=failure_line,
        keep_demand_count=keep_demand_count,
        title=cluster_title,
        problem_texts=problem_texts,
        in_scope_section=in_scope_section,
    )
    if not product_ready:
        limitations.extend(product_blockers)
        limitations.append(
            "Hidden from ranked opportunities until product-ready under ASSETS.md."
        )

    limitations.append(
        "Tatvic score is a prioritization heuristic for offerings "
        "investigation, not market validation."
    )

    return TatvicOpportunityScore(
        # Only product-ready clusters get a published score (Discover / Top N).
        score=(
            score
            if product_ready and eligible_analyses and in_scope_section
            else None
        ),
        weight_version=WEIGHT_VERSION,
        evidence_level=evidence_level,
        components=components,
        component_contributions=contributions,
        reasons=reasons,
        limitations=limitations,
        top10_eligible=top10_eligible,
        product_ready=product_ready,
        single_source_capped=single_source_capped,
        offering_angle=offering_angle,
        smallest_first_version=smallest_v1 if product_ready else None,
        first_client_type=client_type if product_ready else None,
        failure_risk_line=failure_line if product_ready else None,
        section=primary_section,
        demand_metrics={
            "item_count": item_count,
            "distinct_authors": distinct_authors,
            "distinct_source_platforms": platform_count,
            "records_last_30d": records_last_30d,
            "records_prior_30d": records_prior_30d,
            "month_over_month_rising": month_over_month_rising,
            "keep_demand_count": keep_demand_count,
        },
    )


def _offering_angle(
    components: dict[str, int],
    analyses: list[dict[str, Any]],
    in_scope: bool,
) -> tuple[OfferingAngle, str | None, str | None, str | None]:
    if not in_scope or not analyses:
        return "undetermined", None, None, None

    leverage = components.get("leverage_on_offerings", 1)
    demand = components["demand_strength"]
    readiness = components["capability_readiness"]
    commercial_strong = components.get("commercial_signal", 1) >= 4
    first = analyses[0]

    def _clean(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            if value.strip().casefold().startswith("n/a"):
                return None
            return value.strip()
        return None

    smallest = _clean(first.get("smallest_first_version"))
    client = _clean(first.get("first_client_type")) or _clean(
        first.get("affected_user")
    )
    failure = _clean(first.get("failure_risk_line"))

    # Stage 1: All three fields present — use existing angle-selection logic unchanged.
    if smallest and client and failure:
        offering_blob = " ".join(
            str(analyses[0].get(key) or "")
            for key in (
                "smallest_first_version",
                "filter_reason_sentence",
                "context",
                "problem_statement",
            )
        )
        for item in analyses:
            for key in ("component_evidence", "tags"):
                value = item.get(key)
                if isinstance(value, list):
                    offering_blob += " " + " ".join(str(v) for v in value)

        native_solved = any(
            item.get("filter_reason_code")
            in {"NATIVE_SOLVED", "DROP_NATIVE_SOLVED", "SUPPORTING_ONLY"}
            and "native" in str(item.get("filter_reason_sentence") or "").casefold()
            for item in analyses
        )
        if native_solved and cites_known_offering(offering_blob, smallest):
            return (
                "cheaper_delivery",
                smallest[:200],
                client[:200],
                failure[:300],
            )

        if (
            leverage >= 4
            and readiness >= 3
            and cites_known_offering(offering_blob, smallest)
        ):
            return (
                "upgrade_existing_offering",
                smallest[:200],
                client[:200],
                failure[:300],
            )

        if (
            demand >= 3
            and (readiness >= 3 or commercial_strong)
            and leverage >= 3
            and cites_known_offering(offering_blob, smallest)
        ):
            return (
                "new_agent_or_offering",
                smallest[:200],
                client[:200],
                failure[:300],
            )

        return "undetermined", smallest, client, failure

    # Stage 2: One or more fields absent — attempt score-based fallback when in_scope=True.
    # (in_scope=False already returns "undetermined" at the top of this function.)
    offering_blob = (smallest or "") + " " + (client or "") + " " + (failure or "")

    if leverage >= 4 and cites_known_offering(offering_blob, smallest or ""):
        return (
            "upgrade_existing_offering",
            smallest,
            client,
            failure,
        )

    if demand >= 3 and (readiness >= 3 or commercial_strong) and leverage >= 3:
        return (
            "new_agent_or_offering",
            smallest,
            client,
            failure,
        )

    native_solved = any(
        item.get("filter_reason_code")
        in {"NATIVE_SOLVED", "DROP_NATIVE_SOLVED", "SUPPORTING_ONLY"}
        and "native" in str(item.get("filter_reason_sentence") or "").casefold()
        for item in analyses
    )
    if native_solved:
        return (
            "cheaper_delivery",
            smallest,
            client,
            failure,
        )

    return "undetermined", smallest, client, failure


def count_distinct_authors(submissions: list[Any]) -> int:
    authors: set[str] = set()
    for submission in submissions:
        author = getattr(submission, "submitted_by", None)
        if isinstance(author, str) and author.strip():
            authors.add(author.strip().casefold())
        else:
            authors.add(f"submission:{getattr(submission, 'id', 'unknown')}")
    return len(authors)


def count_records_in_window(
    submissions: list[Any],
    *,
    end: datetime,
    days: int,
) -> int:
    start = end - timedelta(days=days)
    count = 0
    for submission in submissions:
        created = getattr(submission, "created_at", None)
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if start <= created < end:
            count += 1
    return count
