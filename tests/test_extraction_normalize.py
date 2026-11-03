import pytest

from app.extraction_normalize import normalize_extraction_payload
from app.schemas import ProblemExtraction


def _base_payload(**overrides):
    payload = {
        "is_genuine_problem": False,
        "signal_type": "general_discussion",
        "problem_statement": "Tutorial video, not a practitioner pain point.",
        "affected_user": "General web user",
        "context": "YouTube how-to content.",
        "category": "General",
        "industry": "General",
        "rejection_reason": "Tutorial content.",
        "urgency_1_to_5": 2,
        "frequency_signal_1_to_5": 2,
        "evidence_strength_1_to_5": 2,
    }
    payload.update(overrides)
    return payload


def test_normalizes_qwen_style_enums_and_lists():
    messy = _base_payload(
        willingness_to_pay_signal="None",
        item_kind="tutorial",
        component_evidence="1 source (YouTube tutorial)",
        software_buildability="Not applicable",
        uncertainty_notes="None; clear rejection based on section scope.",
    )
    normalized = normalize_extraction_payload(messy)
    extraction = ProblemExtraction.model_validate(normalized)

    assert extraction.willingness_to_pay_signal == "none"
    assert extraction.item_kind == "demand"
    assert extraction.software_buildability == "unclear"
    assert extraction.component_evidence == ["1 source (YouTube tutorial)"]
    assert len(extraction.uncertainty_notes) == 1
    assert extraction.category == "Other"


def test_normalizes_nullish_strings_on_optional_fields():
    messy = _base_payload(
        current_workaround="None",
        solution_request="null",
        willingness_to_pay_signal="unclear",
        item_kind="demand",
        software_buildability="unclear",
    )
    normalized = normalize_extraction_payload(messy)
    extraction = ProblemExtraction.model_validate(normalized)

    assert extraction.current_workaround is None
    assert extraction.solution_request is None


def test_fills_rejection_reason_when_missing():
    messy = _base_payload(rejection_reason=None)
    normalized = normalize_extraction_payload(messy)
    extraction = ProblemExtraction.model_validate(normalized)

    assert extraction.rejection_reason


def test_rejects_non_dict():
    with pytest.raises(TypeError):
        normalize_extraction_payload([])  # type: ignore[arg-type]


_QUALITATIVE = (
    "capability_readiness_1_to_5",
    "section_client_fit_1_to_5",
    "leverage_on_offerings_1_to_5",
    "commercial_signal_1_to_5",
    "effort_to_first_version_1_to_5",
    "defensibility_1_to_5",
)


def test_optional_qualitative_valid_scores_preserved():
    normalized = normalize_extraction_payload(
        _base_payload(
            capability_readiness_1_to_5=5,
            section_client_fit_1_to_5=4,
            leverage_on_offerings_1_to_5=3,
            commercial_signal_1_to_5=2,
            effort_to_first_version_1_to_5=1,
            defensibility_1_to_5=5,
        )
    )
    extraction = ProblemExtraction.model_validate(normalized)
    assert extraction.capability_readiness_1_to_5 == 5
    assert extraction.section_client_fit_1_to_5 == 4
    assert extraction.leverage_on_offerings_1_to_5 == 3
    assert extraction.commercial_signal_1_to_5 == 2
    assert extraction.effort_to_first_version_1_to_5 == 1
    assert extraction.defensibility_1_to_5 == 5


def test_optional_qualitative_missing_and_null_stay_none():
    without = normalize_extraction_payload(_base_payload())
    extraction = ProblemExtraction.model_validate(without)
    for key in _QUALITATIVE:
        assert getattr(extraction, key) is None

    nullish = normalize_extraction_payload(
        _base_payload(
            capability_readiness_1_to_5=None,
            effort_to_first_version_1_to_5="None",
            section_client_fit_1_to_5="null",
            leverage_on_offerings_1_to_5="",
        )
    )
    extraction = ProblemExtraction.model_validate(nullish)
    assert extraction.capability_readiness_1_to_5 is None
    assert extraction.effort_to_first_version_1_to_5 is None
    assert extraction.section_client_fit_1_to_5 is None
    assert extraction.leverage_on_offerings_1_to_5 is None


def test_optional_qualitative_malformed_not_coerced_to_three():
    """Malformed present values must not become invent mid-scale scores."""
    normalized = normalize_extraction_payload(
        _base_payload(
            capability_readiness_1_to_5="high",
            section_client_fit_1_to_5="medium",
            leverage_on_offerings_1_to_5="n/a",
            commercial_signal_1_to_5={},
            effort_to_first_version_1_to_5="soon",
            defensibility_1_to_5=["3"],
        )
    )
    extraction = ProblemExtraction.model_validate(normalized)
    for key in _QUALITATIVE:
        assert getattr(extraction, key) is None
    assert all(normalized[key] is None for key in _QUALITATIVE)


def test_optional_qualitative_out_of_range_and_bool_are_invalid():
    normalized = normalize_extraction_payload(
        _base_payload(
            capability_readiness_1_to_5=0,
            section_client_fit_1_to_5=9,
            leverage_on_offerings_1_to_5=True,
            commercial_signal_1_to_5=False,
            effort_to_first_version_1_to_5=3.5,
            defensibility_1_to_5="4.2",
        )
    )
    extraction = ProblemExtraction.model_validate(normalized)
    for key in _QUALITATIVE:
        assert getattr(extraction, key) is None


def test_optional_effort_follows_same_rule_as_other_components():
    valid = normalize_extraction_payload(
        _base_payload(effort_to_first_version_1_to_5="4")
    )
    assert ProblemExtraction.model_validate(valid).effort_to_first_version_1_to_5 == 4

    bad = normalize_extraction_payload(
        _base_payload(effort_to_first_version_1_to_5="quick")
    )
    assert ProblemExtraction.model_validate(bad).effort_to_first_version_1_to_5 is None


def test_malformed_optional_scores_reach_scoring_as_no_evidence():
    """End-to-end: normalizer None → tatvic scoring applies 1 + no evidence."""
    from app.tatvic_scoring import score_tatvic_opportunity

    normalized = normalize_extraction_payload(
        _base_payload(
            is_genuine_problem=True,
            signal_type="pain_point",
            filter_reason_code="KEEP_DEMAND",
            section="Analytics",
            problem_statement="GA4 purchases do not match store orders.",
            capability_readiness_1_to_5="high",
            section_client_fit_1_to_5=4,
            leverage_on_offerings_1_to_5="bad",
            commercial_signal_1_to_5=2,
            effort_to_first_version_1_to_5="soon",
            defensibility_1_to_5=1,
            smallest_first_version="BQ Agent drift report",
            first_client_type="eCommerce analytics lead",
            failure_risk_line="Native GA4 tools may suffice",
            rejection_reason=None,
        )
    )
    row = {
        **normalized,
        "filter_reason_code": "KEEP_DEMAND",
        "section": "Analytics",
        "smallest_first_version": "BQ Agent drift report",
        "first_client_type": "eCommerce analytics lead",
        "failure_risk_line": "Native GA4 tools may suffice",
    }
    result = score_tatvic_opportunity(
        eligible_analyses=[row] * 3,
        submissions=[],
        source_platforms={"reddit", "stack_exchange"},
        item_count=3,
        distinct_authors=3,
        records_last_30d=3,
        records_prior_30d=1,
        primary_section="Analytics",
        cluster_title="GA4 purchase reconciliation",
    )
    assert row["capability_readiness_1_to_5"] is None
    assert row["effort_to_first_version_1_to_5"] is None
    assert row["leverage_on_offerings_1_to_5"] is None
    assert result.components["capability_readiness"] == 1
    assert result.components["effort_to_first_version"] == 1
    assert result.components["leverage_on_offerings"] == 1
    assert result.components["section_client_fit"] == 4
    assert result.components["commercial_signal"] == 2
    assert any("capability_readiness: no evidence" in line for line in result.limitations)
    assert any("effort_to_first_version: no evidence" in line for line in result.limitations)


def test_delivery_fields_normalize_for_executive_output():
    messy = _base_payload(
        is_genuine_problem=True,
        signal_type="pain_point",
        filter_reason_code="KEEP_DEMAND",
        why_now="None",
        first_customer_reach_strategy="  LinkedIn intro via India D2C founder groups  ",
        delivery_type="Standalone Product",
    )
    normalized = normalize_extraction_payload(messy)
    extraction = ProblemExtraction.model_validate(normalized)
    assert extraction.why_now is None
    assert "founder groups" in extraction.first_customer_reach_strategy
    assert extraction.delivery_type == "product"


def test_n1_idea_context_is_a_valid_filter_reason():
    from typing import get_args

    from app.schemas import FilterReasonCode

    normalized = normalize_extraction_payload(
        _base_payload(
            is_genuine_problem=True,
            signal_type="pain_point",
            filter_reason_code="N1_IDEA_CONTEXT",
        )
    )
    extraction = ProblemExtraction.model_validate(normalized)
    assert extraction.filter_reason_code == "N1_IDEA_CONTEXT"
    assert "N1_IDEA_CONTEXT" in get_args(FilterReasonCode)
