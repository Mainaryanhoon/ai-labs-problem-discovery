
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpportunityScore:
    score: float | None
    evidence_level: str
    reasons: list[str]
    limitations: list[str]
    software_buildability: str
    eligibility: str


def _bounded_score(value: Any) -> int | None:
    """Accept only integer scores in the documented range."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= 5:
        return None
    return value


# `initial_evidence` is a legacy label for a cluster's original supporting record.
SUPPORTING_RELATIONSHIPS = frozenset({"supports", "initial_evidence"})


ACTIONABLE_SIGNAL_TYPES = {
    "pain_point",
    "explicit_tool_request",
    "workaround_or_manual_work",
    "feature_gap",
    "buying_signal",
}


def score_opportunity(
    analysis: dict[str, Any],
    *,
    distinct_sources: int = 1,
    verified_independent_sources: int = 0,
    source_timestamped_records_7d: int = 0,
) -> OpportunityScore:
    """
    Rank a candidate for further investigation, not for market validation.

    Source counts must be supplied by trusted application logic, not Gemini.
    A distinct source is not necessarily an independent person.
    """
    urgency = _bounded_score(analysis.get("urgency_1_to_5"))
    frequency = _bounded_score(analysis.get("frequency_signal_1_to_5"))
    evidence = _bounded_score(analysis.get("evidence_strength_1_to_5"))

    genuine = analysis.get("is_genuine_problem") is True
    signal_type = analysis.get("signal_type", "general_discussion")
    buying_signal = analysis.get("willingness_to_pay_signal", "unclear")
    buildability = analysis.get("software_buildability", "unclear")

    reasons = []
    limitations = []

    if (
        not genuine
        or not isinstance(signal_type, str)
        or signal_type not in ACTIONABLE_SIGNAL_TYPES
    ):
        return OpportunityScore(
            score=0.0,
            evidence_level="rejected_or_unqualified",
            reasons=["Not classified as an actionable problem."],
            limitations=["Classification is model-generated and may be wrong."],
            software_buildability=(
                buildability if isinstance(buildability, str) else "unclear"
            ),
            eligibility="ineligible",
        )

    if buildability == "unlikely":
        return OpportunityScore(
            score=None,
            evidence_level="not_software_buildable",
            reasons=[],
            limitations=[
                "The described need is not currently classified as "
                "software-buildable."
            ],
            software_buildability="unlikely",
            eligibility="ineligible",
        )
    if buildability != "likely":
        return OpportunityScore(
            score=None,
            evidence_level="buildability_unclear",
            reasons=[],
            limitations=[
                "Software buildability is unclear; review is required before "
                "ranking this candidate."
            ],
            software_buildability="unclear",
            eligibility="review_required",
        )

    if not any(value is not None for value in (urgency, frequency, evidence)):
        return OpportunityScore(
            score=None,
            evidence_level="insufficient_evidence",
            reasons=[],
            limitations=[
                "No valid urgency, frequency, or evidence-strength ratings "
                "were supplied.",
                "A score cannot be calculated from missing or invalid ratings.",
            ],
            software_buildability="likely",
            eligibility="eligible",
        )

    urgency = urgency if urgency is not None else 1
    frequency = frequency if frequency is not None else 1
    evidence = evidence if evidence is not None else 1
    if any(
        _bounded_score(analysis.get(key)) is None
        for key in (
            "urgency_1_to_5",
            "frequency_signal_1_to_5",
            "evidence_strength_1_to_5",
        )
    ):
        limitations.append(
            "One or more ratings were missing or invalid and contributed "
            "the minimum value."
        )

    # Weighted heuristic, not a probability or validated market score.
    score = (
        0.30 * urgency
        + 0.25 * frequency
        + 0.20 * evidence
    ) / 5 * 100
    reasons.append(
        "Base score uses urgency (30%), frequency (25%), and evidence "
        "strength (20%)."
    )

    valid_recent_records = (
        source_timestamped_records_7d
        if isinstance(source_timestamped_records_7d, int)
        and not isinstance(source_timestamped_records_7d, bool)
        and source_timestamped_records_7d >= 0
        else 0
    )
    growth_points = min(15, valid_recent_records * 5)
    if growth_points:
        score += growth_points
        reasons.append(
            f"Recent source-timestamped evidence adds {growth_points} "
            "growth points (up to 15)."
        )
    else:
        limitations.append(
            "No source-timestamped evidence was recorded in the last 7 days."
        )

    valid_distinct_sources = (
        distinct_sources
        if isinstance(distinct_sources, int)
        and not isinstance(distinct_sources, bool)
        and distinct_sources >= 0
        else 0
    )
    requested_verified_sources = (
        verified_independent_sources
        if isinstance(verified_independent_sources, int)
        and not isinstance(verified_independent_sources, bool)
        and verified_independent_sources >= 0
        else 0
    )
    valid_verified_sources = min(
        requested_verified_sources,
        valid_distinct_sources,
    )
    quotes = analysis.get("evidence_quotes")
    has_quotes = (
        isinstance(quotes, list)
        and any(isinstance(quote, str) and quote.strip() for quote in quotes)
    )

    if valid_distinct_sources >= 2:
        score += 5
        reasons.append("Evidence exists across multiple distinct source records.")
    elif valid_distinct_sources == 1:
        limitations.append(
            "Only one distinct source record; demand is not corroborated."
        )
    else:
        limitations.append(
            "No usable source identifier is available for corroboration."
        )

    if valid_verified_sources >= 2:
        score += 10
        reasons.append("At least two sources have verified independence.")
    else:
        limitations.append(
            "Independent demand has not been sufficiently verified."
        )

    if buying_signal == "explicit" and has_quotes:
        score += 10
        reasons.append(
            "At least one included record reports explicit purchase or budget intent."
        )
    elif buying_signal == "suggested" and has_quotes:
        score += 3
        reasons.append(
            "At least one included record reports indirect commercial evidence."
        )
    elif (
        isinstance(buying_signal, str)
        and buying_signal in {"explicit", "suggested"}
    ):
        limitations.append(
            "Purchase intent was not credited without a traceable evidence "
            "excerpt; commercial intent may be absent or unclear."
        )
    else:
        limitations.append(
            "No explicit willingness-to-pay evidence is established; "
            "commercial intent may be absent or unclear."
        )

    if not has_quotes:
        score -= 10
        limitations.append("No direct evidence excerpts were supplied.")

    score = round(max(0.0, min(100.0, score)), 1)

    if valid_verified_sources >= 2:
        evidence_level = "corroborated_candidate"
    elif valid_distinct_sources >= 2:
        evidence_level = "multi_source_unverified"
    elif valid_distinct_sources == 1:
        evidence_level = "single_source_candidate"
    else:
        evidence_level = "source_identity_unverified"

    limitations.append(
        "This is a prioritization heuristic, not a measure of market size, "
        "probability of success, or product-market fit; it is not statistically "
        "calibrated."
    )

    return OpportunityScore(
        score=score,
        evidence_level=evidence_level,
        reasons=reasons,
        limitations=limitations,
        software_buildability="likely",
        eligibility="eligible",
    )
