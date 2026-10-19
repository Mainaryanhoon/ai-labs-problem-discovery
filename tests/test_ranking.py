
from app.ranking import score_opportunity


def test_single_post_is_only_a_candidate():
    result = score_opportunity({
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "urgency_1_to_5": 5,
        "frequency_signal_1_to_5": 5,
        "evidence_strength_1_to_5": 5,
        "willingness_to_pay_signal": "none",
        "evidence_quotes": ["I spend hours reconciling orders."],
    })

    assert result.evidence_level == "single_source_candidate"
    assert result.score < 100
    assert any(
        "not corroborated" in item.lower()
        for item in result.limitations
    )


def test_noise_is_not_ranked_as_an_opportunity():
    result = score_opportunity({
        "is_genuine_problem": False,
        "signal_type": "noise",
    })

    assert result.score == 0
    assert result.evidence_level == "rejected_or_unqualified"


def test_verified_independent_evidence_changes_rank():
    analysis = {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "urgency_1_to_5": 3,
        "frequency_signal_1_to_5": 3,
        "evidence_strength_1_to_5": 3,
        "willingness_to_pay_signal": "none",
        "evidence_quotes": ["Matching sales takes hours."],
    }

    single = score_opportunity(analysis)
    corroborated = score_opportunity(
        analysis,
        distinct_sources=3,
        verified_independent_sources=2,
    )

    assert corroborated.score > single.score
    assert corroborated.evidence_level == "corroborated_candidate"
