"""Versioned commercial scorer ``v0.2-commercial`` (inactive for ranking).

Active product ranking is ``app.tatvic_scoring`` v0.1 with commercial-first
weights (commercial_signal 35, india_relevance 15). This module must never
overwrite those v0.1 scores; it is comparison-only until owners activate it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COMMERCIAL_WEIGHT_VERSION = "v0.2-commercial"

COMMERCIAL_COMPONENT_WEIGHTS: dict[str, int] = {
    "commercial_demand_buying": 30,
    "problem_significance_recurrence": 25,
    "tatvic_fit_delivery": 20,
    "timing_market_momentum": 15,
    "buyer_identification_path": 10,
}


def _bounded(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= 5:
        return None
    return value


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[(len(values) - 1) // 2]


@dataclass(frozen=True)
class CommercialOpportunityScoreV02:
    score: float | None
    weight_version: str
    components: dict[str, int]
    component_contributions: dict[str, float]
    reasons: list[str]
    limitations: list[str]
    active_for_ranking: bool = False


def score_commercial_opportunity_v02(
    *,
    demand_analyses: list[dict[str, Any]],
    demand_submissions: list[Any],
    supporting_capability_count: int,
    product_ready: bool,
    offering_angle: str | None,
    qualification: dict[str, Any],
) -> dict[str, Any]:
    """Compute informational v0.2 commercial score from demand-role evidence."""
    limitations: list[str] = []
    reasons: list[str] = []

    # --- Commercial demand / buying (1–5) ---
    commercial_vals = [
        v
        for item in demand_analyses
        if (v := _bounded(item.get("commercial_signal_1_to_5"))) is not None
    ]
    buying = _median(commercial_vals)
    if buying is None:
        buying = 1
        limitations.append("commercial_demand_buying: no evidence")
        reasons.append("commercial_demand_buying scored 1/5 (no evidence).")
    else:
        # Practitioner commercial_signal alone is weak buying proof.
        if buying >= 4:
            reasons.append(
                "High model commercial_signal present; still requires human "
                "review — not procurement proof."
            )
            limitations.append(
                "Buying evidence is model-reported on practitioner posts, "
                "not explicit procurement."
            )

    # --- Problem significance / recurrence ---
    item_count = len(demand_analyses)
    authors = {
        (getattr(s, "submitted_by", None) or "").strip()
        for s in demand_submissions
        if (getattr(s, "submitted_by", None) or "").strip()
    }
    platforms = {
        getattr(s, "source_name", None)
        for s in demand_submissions
        if getattr(s, "source_name", None)
    }
    if item_count >= 5 and len(authors) >= 3 and len(platforms) >= 2:
        significance = 5
    elif item_count >= 3 and len(authors) >= 2:
        significance = 4
    elif item_count >= 2:
        significance = 3
    elif item_count == 1:
        significance = 2
    else:
        significance = 1
        limitations.append("problem_significance_recurrence: no evidence")
    reasons.append(
        f"problem_significance_recurrence {significance}/5 from "
        f"{item_count} demand-role items, {len(authors)} authors, "
        f"{len(platforms)} demand platforms "
        f"(+{supporting_capability_count} capability corroboration)."
    )

    # --- Tatvic fit / delivery ---
    leverage_vals = [
        v
        for item in demand_analyses
        if (v := _bounded(item.get("leverage_on_offerings_1_to_5"))) is not None
    ]
    fit_vals = [
        v
        for item in demand_analyses
        if (v := _bounded(item.get("section_client_fit_1_to_5"))) is not None
    ]
    readiness_vals = [
        v
        for item in demand_analyses
        if (v := _bounded(item.get("capability_readiness_1_to_5"))) is not None
    ]
    fit_parts = [v for v in (_median(leverage_vals), _median(fit_vals), _median(readiness_vals)) if v]
    if fit_parts:
        tatvic_fit = max(1, min(5, int(round(sum(fit_parts) / len(fit_parts)))))
    else:
        tatvic_fit = 1
        limitations.append("tatvic_fit_delivery: no evidence")
    if offering_angle in {None, "undetermined"}:
        tatvic_fit = min(tatvic_fit, 2)
        limitations.append("No clear offering angle; Tatvic fit capped.")
    if not product_ready:
        reasons.append("Cluster is not product-ready under active v0.1 gates.")

    # --- Timing / momentum ---
    # Without company triggers from approved sources, score 1 unless demand
    # items have recent source timestamps (weak proxy only).
    timing = 1
    limitations.append("timing_market_momentum: no evidence")
    reasons.append(
        "timing_market_momentum scored 1/5 (no dated company trigger in "
        "approved sources)."
    )
    trigger = qualification.get("business_trigger")
    trigger_date = qualification.get("trigger_date")
    if isinstance(trigger, str) and trigger.strip() and trigger_date:
        timing = 4
        limitations = [item for item in limitations if "timing_market_momentum" not in item]
        reasons.append("Dated business trigger present in qualification.")

    # --- Buyer identification / path ---
    buyer = 1
    if qualification.get("verified_outreach_route"):
        buyer = 5
    elif qualification.get("buyer_name") and qualification.get("buyer_function"):
        buyer = 4
    elif isinstance(qualification.get("buyer_function"), str) and (
        qualification.get("buyer_function") or ""
    ).strip():
        buyer = 2
        limitations.append(
            "buyer_identification_path: role inferred from first_client_type "
            "only; no verified outreach route."
        )
    else:
        limitations.append("buyer_identification_path: no evidence")
        reasons.append("buyer_identification_path scored 1/5 (no evidence).")

    components = {
        "commercial_demand_buying": buying,
        "problem_significance_recurrence": significance,
        "tatvic_fit_delivery": tatvic_fit,
        "timing_market_momentum": timing,
        "buyer_identification_path": buyer,
    }
    contributions: dict[str, float] = {}
    total = 0.0
    for key, weight in COMMERCIAL_COMPONENT_WEIGHTS.items():
        part = weight * (components[key] - 1) / 4
        contributions[key] = round(part, 2)
        total += part

    return {
        "score": round(total, 1),
        "weight_version": COMMERCIAL_WEIGHT_VERSION,
        "components": components,
        "component_contributions": contributions,
        "reasons": reasons,
        "limitations": limitations,
        "active_for_ranking": False,
        "india_relevance": qualification.get("india_relevance"),
        "is_commercial_lead": bool(qualification.get("is_commercial_lead")),
    }
