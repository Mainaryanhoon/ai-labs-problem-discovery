
import pytest

from app.ranking import score_opportunity


def valid_problem(**overrides):
    analysis = {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "urgency_1_to_5": 3,
        "frequency_signal_1_to_5": 3,
        "evidence_strength_1_to_5": 3,
        "willingness_to_pay_signal": "none",
        "software_buildability": "likely",
        "evidence_quotes": ["Matching sales takes hours."],
    }
    analysis.update(overrides)
    return analysis


def test_single_post_is_only_a_candidate():
    result = score_opportunity(
        valid_problem(
            urgency_1_to_5=5,
            frequency_signal_1_to_5=5,
            evidence_strength_1_to_5=5,
        )
    )

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


@pytest.mark.parametrize(
    "analysis",
    [
        valid_problem(is_genuine_problem=False),
        valid_problem(signal_type="noise"),
        valid_problem(signal_type="general_discussion"),
        valid_problem(signal_type="unknown"),
        valid_problem(signal_type=["pain_point"]),
    ],
)
def test_ineligible_analysis_never_receives_positive_score(analysis):
    result = score_opportunity(analysis)

    assert result.score == 0
    assert result.evidence_level == "rejected_or_unqualified"


@pytest.mark.parametrize(
    "rating",
    [None, True, False, 0, 6, 2.5, "5"],
)
def test_invalid_ratings_are_not_accepted_as_numeric_evidence(rating):
    result = score_opportunity(
        valid_problem(
            urgency_1_to_5=rating,
            frequency_signal_1_to_5=None,
            evidence_strength_1_to_5=None,
        )
    )

    assert result.score is None
    assert result.evidence_level == "insufficient_evidence"


def test_partially_missing_ratings_use_minimum_and_are_explained():
    result = score_opportunity(
        valid_problem(
            urgency_1_to_5=5,
            frequency_signal_1_to_5=None,
            evidence_strength_1_to_5=0,
            evidence_quotes=[],
        )
    )

    assert result.score is not None
    assert any("minimum value" in text for text in result.limitations)
    assert result.score < 40


@pytest.mark.parametrize(
    ("signal", "expected_delta"),
    [
        ("explicit", 10),
        ("suggested", 3),
        ("none", 0),
        ("unclear", 0),
        ("invalid", 0),
    ],
)
def test_commercial_signal_bonus_requires_a_supported_category(
    signal, expected_delta
):
    base = score_opportunity(
        valid_problem(willingness_to_pay_signal="none")
    )
    actual = score_opportunity(
        valid_problem(willingness_to_pay_signal=signal)
    )

    assert actual.score - base.score == expected_delta


def test_tool_request_does_not_create_a_commercial_signal():
    result = score_opportunity(
        valid_problem(
            signal_type="explicit_tool_request",
            willingness_to_pay_signal="none",
        )
    )

    assert "purchase intent" not in " ".join(result.reasons).lower()
    assert any("willingness-to-pay" in text for text in result.limitations)


def test_quote_absence_costs_score_and_reports_limitation():
    with_quote = score_opportunity(valid_problem())
    without_quote = score_opportunity(valid_problem(evidence_quotes=[]))

    assert with_quote.score - without_quote.score == 10
    assert any("excerpts" in text for text in without_quote.limitations)


def test_purchase_intent_without_quotes_does_not_receive_bonus():
    baseline = score_opportunity(
        valid_problem(willingness_to_pay_signal="none", evidence_quotes=[])
    )
    unsupported = score_opportunity(
        valid_problem(willingness_to_pay_signal="explicit", evidence_quotes=[])
    )

    assert unsupported.score == baseline.score
    assert any(
        "Purchase intent was not credited" in text
        for text in unsupported.limitations
    )


def test_invalid_source_counts_do_not_add_corroboration_bonus():
    result = score_opportunity(
        valid_problem(),
        distinct_sources=True,
        verified_independent_sources=True,
    )

    assert result.evidence_level == "source_identity_unverified"
    assert not any("multiple distinct" in text for text in result.reasons)


def test_verified_independent_evidence_changes_rank():
    analysis = valid_problem()

    single = score_opportunity(analysis)
    corroborated = score_opportunity(
        analysis,
        distinct_sources=3,
        verified_independent_sources=2,
    )

    assert corroborated.score > single.score
    assert corroborated.evidence_level == "corroborated_candidate"


@pytest.mark.parametrize("buildability", ["unclear", "unlikely", None])
def test_unconfirmed_software_buildability_is_not_ranked(buildability):
    result = score_opportunity(
        valid_problem(software_buildability=buildability)
    )

    assert result.score is None
    assert result.eligibility != "eligible"


def test_recent_source_timestamps_are_an_explainable_capped_component():
    analysis = valid_problem()
    baseline = score_opportunity(analysis)
    one_recent = score_opportunity(
        analysis,
        source_timestamped_records_7d=1,
    )
    many_recent = score_opportunity(
        analysis,
        source_timestamped_records_7d=10,
    )
    invalid_count = score_opportunity(
        analysis,
        source_timestamped_records_7d=True,
    )

    assert one_recent.score - baseline.score == 5
    assert many_recent.score - baseline.score == 15
    assert invalid_count.score == baseline.score
    assert any("growth points" in reason for reason in many_recent.reasons)
