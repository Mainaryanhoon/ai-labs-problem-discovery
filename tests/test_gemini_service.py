import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.genai.errors import ClientError, ServerError

from app.schemas import ProblemExtraction
from app.services import gemini


SOURCE_TEXT = (
    "I manually reconcile orders every Friday and spend hours fixing "
    "mistakes."
)


def extraction_payload(**overrides):
    payload = {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "solution_request": None,
        "willingness_to_pay_signal": "none",
        "evidence_quotes": ["manually reconcile orders every Friday"],
        "noise_reason": None,
        "rejection_reason": None,
        "problem_statement": "Reconciling orders manually takes significant time.",
        "affected_user": "Unclear from submission",
        "context": "The author spends hours reconciling orders.",
        "current_workaround": "Manual reconciliation",
        "category": "Business Operations",
        "industry": "Unclear",
        "software_buildability": "likely",
        "software_buildability_reason": "The workflow can be automated.",
        "urgency_1_to_5": 3,
        "frequency_signal_1_to_5": 2,
        "evidence_strength_1_to_5": 3,
        "tags": [],
        "uncertainty_notes": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def gemini_client():
    with patch(
        "app.services.gemini.get_settings",
        return_value=SimpleNamespace(
            gemini_api_key="test-key",
            gemini_model="test-model",
        ),
    ), patch("app.services.gemini.genai.Client") as client_factory:
        client = client_factory.return_value
        yield client


@pytest.mark.parametrize("signal", ["explicit", "suggested"])
def test_analysis_preserves_willingness_to_pay_categories(
    gemini_client, signal
):
    payload = extraction_payload(willingness_to_pay_signal=signal)
    gemini_client.models.generate_content.return_value.text = json.dumps(payload)

    result, model_name = gemini.analyze_submission(
        SOURCE_TEXT,
        "test",
        "en",
    )

    assert result.willingness_to_pay_signal == signal
    assert model_name == "test-model"


def test_prompt_requires_conservative_scoring_and_traceable_evidence(
    gemini_client,
):
    gemini_client.models.generate_content.return_value.text = json.dumps(
        extraction_payload()
    )

    gemini.analyze_submission(SOURCE_TEXT, "test", "en")

    prompt = gemini_client.models.generate_content.call_args.kwargs["contents"]
    assert "Every quote must appear verbatim" in prompt
    assert "not votes," in prompt
    assert "Do not infer repeated demand from one post." in prompt
    assert "founder's research" in prompt
    assert "software_buildability" in prompt
    assert "Finance & Accounting" in prompt


def test_problem_extraction_rejects_noncanonical_categories():
    payload = extraction_payload(category="Operations")

    with pytest.raises(ValueError):
        ProblemExtraction.model_validate(payload)


def test_problem_extraction_defaults_missing_buildability_to_unclear():
    payload = extraction_payload()
    payload.pop("software_buildability")
    payload.pop("software_buildability_reason")

    extraction = ProblemExtraction.model_validate(payload)

    assert extraction.software_buildability == "unclear"


def test_prompt_covers_social_context_and_ambiguous_complaints(gemini_client):
    gemini_client.models.generate_content.return_value.text = json.dumps(
        extraction_payload(
            is_genuine_problem=False,
            signal_type="general_discussion",
            evidence_quotes=[],
            rejection_reason="No actionable need is described.",
        )
    )

    result, _ = gemini.analyze_submission(
        "This is so frustrating.",
        "X",
        "en",
        source_context=json.dumps({
            "is_reply": True,
            "author": "support_account",
            "author_is_business_account": True,
        }),
    )

    prompt = gemini_client.models.generate_content.call_args.kwargs["contents"]
    assert "company or support-account reply" in prompt
    assert "provider-marked business account" in prompt
    assert "vague expression of frustration" in prompt
    assert "Speculation, hypothetical ideas" in prompt
    assert "assess the author's own commentary separately" in prompt
    assert '"is_reply": true' in prompt
    assert result.is_genuine_problem is False
    assert result.signal_type == "general_discussion"


def test_repost_metadata_prevents_author_pain_claim(gemini_client):
    gemini_client.models.generate_content.return_value.text = json.dumps(
        extraction_payload(
            is_genuine_problem=True,
            evidence_quotes=["manually reconcile orders every Friday"],
        )
    )

    result, _ = gemini.analyze_submission(
        SOURCE_TEXT,
        "X",
        "en",
        source_context=json.dumps({"is_repost": True}),
    )

    assert result.is_genuine_problem is False
    assert result.signal_type == "general_discussion"
    assert result.willingness_to_pay_signal == "none"
    assert result.evidence_quotes == []
    assert "reposted content" in result.rejection_reason


def test_quote_repost_is_preserved_for_review(gemini_client):
    gemini_client.models.generate_content.return_value.text = json.dumps(
        extraction_payload()
    )

    result, _ = gemini.analyze_submission(
        SOURCE_TEXT,
        "X",
        "en",
        source_context=json.dumps({
            "is_repost": True,
            "is_quote": True,
        }),
    )

    assert result.is_genuine_problem is True
    assert result.signal_type == "pain_point"
    assert result.evidence_quotes[0] in SOURCE_TEXT


def test_specific_customer_pain_with_traceable_quote_remains_eligible(
    gemini_client,
):
    gemini_client.models.generate_content.return_value.text = json.dumps(
        extraction_payload()
    )

    result, _ = gemini.analyze_submission(
        SOURCE_TEXT,
        "X",
        "en",
        source_context=json.dumps({"is_quote": True, "author": "shop_owner"}),
    )

    assert result.is_genuine_problem is True
    assert result.signal_type == "pain_point"
    assert result.evidence_quotes[0] in SOURCE_TEXT


@pytest.mark.parametrize(
    "text",
    [
        "This is so frustrating.",
        "Maybe someone should build an app for this someday.",
    ],
)
def test_mocked_vague_or_speculative_posts_are_general_discussion(
    gemini_client,
    text,
):
    gemini_client.models.generate_content.return_value.text = json.dumps(
        extraction_payload(
            is_genuine_problem=False,
            signal_type="general_discussion",
            evidence_quotes=[],
            rejection_reason="No concrete actionable customer need is stated.",
        )
    )

    result, _ = gemini.analyze_submission(text, "X", "en")

    assert result.is_genuine_problem is False
    assert result.signal_type == "general_discussion"


def test_invalid_json_is_a_controlled_analysis_error(gemini_client):
    gemini_client.models.generate_content.return_value.text = "{invalid"

    with pytest.raises(
        gemini.GeminiAnalysisError,
        match="response failed schema validation",
    ):
        gemini.analyze_submission(SOURCE_TEXT, "test", "en")


def test_unverifiable_quote_is_rejected(gemini_client):
    gemini_client.models.generate_content.return_value.text = json.dumps(
        extraction_payload(evidence_quotes=["invented evidence"])
    )

    with pytest.raises(
        gemini.GeminiAnalysisError,
        match="do not appear verbatim",
    ):
        gemini.analyze_submission(SOURCE_TEXT, "test", "en")


def test_noise_cannot_be_validated_as_a_genuine_problem(gemini_client):
    gemini_client.models.generate_content.return_value.text = json.dumps(
        extraction_payload(
            is_genuine_problem=True,
            signal_type="noise",
            noise_reason="Spam",
        )
    )

    with pytest.raises(
        gemini.GeminiAnalysisError,
        match="response failed schema validation",
    ):
        gemini.analyze_submission(SOURCE_TEXT, "test", "en")


def test_rate_limit_is_retryable_and_not_retried_immediately(gemini_client):
    gemini_client.models.generate_content.side_effect = ClientError(
        429,
        {"error": {"message": "quota exhausted"}},
    )

    with pytest.raises(
        gemini.GeminiRateLimitError,
        match="rate limit or quota exceeded",
    ):
        gemini.analyze_submission(SOURCE_TEXT, "test", "en")

    assert gemini_client.models.generate_content.call_count == 1


def test_missing_configuration_fails_before_creating_client():
    with patch(
        "app.services.gemini.get_settings",
        return_value=SimpleNamespace(
            gemini_api_key="",
            gemini_model="test-model",
        ),
    ), patch("app.services.gemini.genai.Client") as client_factory:
        with pytest.raises(gemini.GeminiNotConfigured):
            gemini.analyze_submission(SOURCE_TEXT, "test", "en")

    client_factory.assert_not_called()


def test_transient_provider_failure_uses_bounded_retry(gemini_client):
    response = SimpleNamespace(text=json.dumps(extraction_payload()))
    gemini_client.models.generate_content.side_effect = [
        ServerError(503, {"error": {"message": "temporarily unavailable"}}),
        response,
    ]

    with patch("app.services.gemini.time.sleep") as sleep:
        result, _ = gemini.analyze_submission(SOURCE_TEXT, "test", "en")

    assert isinstance(result, ProblemExtraction)
    assert gemini_client.models.generate_content.call_count == 2
    sleep.assert_called_once_with(0.5)


def test_transient_provider_failure_stops_at_attempt_limit(gemini_client):
    gemini_client.models.generate_content.side_effect = ServerError(
        503,
        {"error": {"message": "temporarily unavailable"}},
    )

    with patch("app.services.gemini.time.sleep") as sleep:
        with pytest.raises(
            gemini.GeminiAnalysisError,
            match="after 3 attempts",
        ):
            gemini.analyze_submission(SOURCE_TEXT, "test", "en")

    assert gemini_client.models.generate_content.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [0.5, 1.0]


def test_scores_reject_boolean_values():
    payload = extraction_payload(urgency_1_to_5=True)

    with pytest.raises(ValueError):
        ProblemExtraction.model_validate(payload)
