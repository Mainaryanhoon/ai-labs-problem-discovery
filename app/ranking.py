
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpportunityScore:
    score: float
    evidence_level: str
    reasons: list[str]
    limitations: list[str]


def _bounded_score(value: Any, default: int = 1) -> int:
    """Accept only integer scores from 1 to 5."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(1, min(5, value))


def score_opportunity(
    analysis: dict[str, Any],
    *,
    distinct_sources: int = 1,
    verified_independent_sources: int = 0,
) -> OpportunityScore:
    """
    Rank a candidate for further investigation, not for market validation.

    Source counts must be supplied by trusted application logic, not Gemini.
    A distinct source is not necessarily an independent person.
    """
    urgency = _bounded_score(analysis.get("urgency_1_to_5"))
    frequency = _bounded_score(
        analysis.get("frequency_signal_1_to_5")
    )
    evidence = _bounded_score(
        analysis.get("evidence_strength_1_to_5")
    )

    genuine = analysis.get("is_genuine_problem") is True
    signal_type = analysis.get("signal_type", "general_discussion")
    buying_signal = analysis.get("willingness_to_pay_signal", "unclear")

    reasons = []
    limitations = []

    if not genuine or signal_type in {"general_discussion", "noise"}:
        return OpportunityScore(
            score=0.0,
            evidence_level="rejected_or_unqualified",
            reasons=["Not classified as an actionable problem."],
            limitations=["Classification is model-generated and may be wrong."],
        )

    # Weighted heuristic, not a probability or validated market score.
    score = (
        0.30 * urgency
        + 0.25 * frequency
        + 0.20 * evidence
    ) / 5 * 100

    if distinct_sources >= 2:
        score += 5
        reasons.append("Evidence exists across multiple distinct source records.")
    else:
        limitations.append(
            "Only one distinct source record; demand is not corroborated."
        )

    if verified_independent_sources >= 2:
        score += 10
        reasons.append("At least two sources have verified independence.")
    else:
        limitations.append(
            "Independent demand has not been sufficiently verified."
        )

    if buying_signal == "explicit":
        score += 10
        reasons.append("Explicit purchase or budget intent was reported.")
    elif buying_signal == "suggested":
        score += 3
        reasons.append("Some indirect commercial evidence was reported.")
    else:
        limitations.append("No explicit willingness-to-pay evidence.")

    if not analysis.get("evidence_quotes"):
        score -= 10
        limitations.append("No direct evidence excerpts were supplied.")

    score = round(max(0.0, min(100.0, score)), 1)

    if verified_independent_sources >= 2:
        evidence_level = "corroborated_candidate"
    elif distinct_sources >= 2:
        evidence_level = "multi_source_unverified"
    else:
        evidence_level = "single_source_candidate"

    limitations.append(
        "This is a prioritization heuristic, not a measure of market size, "
        "probability of success, or product-market fit."
    )

    return OpportunityScore(
        score=score,
        evidence_level=evidence_level,
        reasons=reasons,
        limitations=limitations,
    )
