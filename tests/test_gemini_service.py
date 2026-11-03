import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.schemas import ProblemExtraction
from app.services import gemini
from app.services.llm import LLMAnalysisError, LLMRateLimitError


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
        "section": "Analytics",
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
def litellm_chat():
    settings = SimpleNamespace(
        litellm_api_key="test-key",
        litellm_base_url="http://192.168.3.200:4000/v1",
        litellm_model="qwen3.8-27b",
        litellm_max_tokens=4096,
        gemini_api_key="",
        gemini_model="unused",
    )
    with patch(
        "app.services.gemini.get_settings",
        return_value=settings,
    ), patch(
        "app.services.gemini.llm_configured",
        return_value=True,
    ), patch(
        "app.services.gemini.chat_completion_json",
    ) as chat:
        yield chat


@pytest.mark.parametrize("signal", ["explicit", "suggested"])
def test_analysis_preserves_willingness_to_pay_categories(litellm_chat, signal):
    payload = extraction_payload(willingness_to_pay_signal=signal)
    litellm_chat.return_value = json.dumps(payload)

    result, model_name = gemini.analyze_submission(
        SOURCE_TEXT,
        "test",
        "en",
    )

    assert result.willingness_to_pay_signal == signal
    assert model_name == "qwen3.8-27b"


def test_prompt_requires_conservative_scoring_and_traceable_evidence(litellm_chat):
    litellm_chat.return_value = json.dumps(extraction_payload())

    gemini.analyze_submission(SOURCE_TEXT, "test", "en")

    kwargs = litellm_chat.call_args.kwargs
    user = kwargs["user"]
    assert "verbatim" in user.casefold() or "evidence_quotes" in user
    assert "Tatvic" in user
    assert "software_buildability" in user
    assert "KEEP_DEMAND" in user
    # OpenClaw six-file brain must be present in the classifier prompt.
    for marker in ("<soul>", "<goal>", "<rubric>", "<assets>", "<sources>", "<agents>"):
        assert marker in user
    assert "SIX-FILE VERIFICATION" in user


def test_problem_extraction_rejects_noncanonical_categories():
    payload = extraction_payload(category="Operations")

    with pytest.raises(ValueError):
        ProblemExtraction.model_validate(payload)


def test_analyze_submission_normalizes_messy_litellm_json(litellm_chat):
    litellm_chat.return_value = json.dumps(
        extraction_payload(
            is_genuine_problem=False,
            signal_type="general_discussion",
            evidence_quotes=[],
            rejection_reason="Tutorial content.",
            willingness_to_pay_signal="None",
            item_kind="tutorial",
            component_evidence="1 source (YouTube tutorial)",
            software_buildability="Not applicable",
            uncertainty_notes="None; out of scope.",
            category="General",
        )
    )

    result, model_name = gemini.analyze_submission(
        "How to Create a Website Feedback Form with AI (Step-by-Step)",
        "youtube",
        "en",
    )

    assert model_name == "qwen3.8-27b"
    assert result.willingness_to_pay_signal == "none"
    assert result.item_kind == "demand"
    assert result.software_buildability == "unclear"
    assert result.component_evidence == ["1 source (YouTube tutorial)"]


def test_problem_extraction_defaults_missing_buildability_to_unclear():
    payload = extraction_payload()
    payload.pop("software_buildability")
    payload.pop("software_buildability_reason")

    extraction = ProblemExtraction.model_validate(payload)

    assert extraction.software_buildability == "unclear"


def test_prompt_covers_social_context_and_ambiguous_complaints(litellm_chat):
    litellm_chat.return_value = json.dumps(
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

    user = litellm_chat.call_args.kwargs["user"]
    assert "is_reply" in user
    assert "Tatvic" in user
    assert result.is_genuine_problem is False
    assert result.signal_type == "general_discussion"


def test_repost_metadata_prevents_author_pain_claim(litellm_chat):
    litellm_chat.return_value = json.dumps(
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


def test_quote_repost_is_preserved_for_review(litellm_chat):
    litellm_chat.return_value = json.dumps(extraction_payload())

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
    litellm_chat,
):
    litellm_chat.return_value = json.dumps(extraction_payload())

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
    litellm_chat,
    text,
):
    litellm_chat.return_value = json.dumps(
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


def test_invalid_json_is_a_controlled_analysis_error(litellm_chat):
    litellm_chat.return_value = "{invalid"

    with pytest.raises(
        gemini.GeminiAnalysisError,
        match="not valid JSON|schema validation",
    ):
        gemini.analyze_submission(SOURCE_TEXT, "test", "en")


def test_unverifiable_quote_is_stripped(litellm_chat):
    litellm_chat.return_value = json.dumps(
        extraction_payload(evidence_quotes=["invented evidence"])
    )

    result, _ = gemini.analyze_submission(SOURCE_TEXT, "test", "en")

    assert result.evidence_quotes == []


def test_noise_cannot_be_validated_as_a_genuine_problem(litellm_chat):
    litellm_chat.return_value = json.dumps(
        extraction_payload(
            is_genuine_problem=True,
            signal_type="noise",
            noise_reason="Spam",
        )
    )

    with pytest.raises(
        gemini.GeminiAnalysisError,
        match="schema validation",
    ):
        gemini.analyze_submission(SOURCE_TEXT, "test", "en")


def test_rate_limit_is_retryable_and_not_retried_immediately(litellm_chat):
    litellm_chat.side_effect = LLMRateLimitError(
        "LiteLLM rate limit or quota exceeded."
    )

    with pytest.raises(
        gemini.GeminiRateLimitError,
        match="rate limit or quota exceeded",
    ):
        gemini.analyze_submission(SOURCE_TEXT, "test", "en")

    assert litellm_chat.call_count == 1


def test_missing_configuration_fails_before_creating_client():
    with patch(
        "app.services.gemini.get_settings",
        return_value=SimpleNamespace(
            litellm_api_key="",
            litellm_base_url="http://192.168.3.200:4000/v1",
            litellm_model="qwen3.8-27b",
            gemini_api_key="",
            gemini_model="test-model",
        ),
    ), patch(
        "app.services.gemini.llm_configured",
        return_value=False,
    ), patch(
        "app.services.gemini.chat_completion_json",
    ) as chat:
        with pytest.raises(gemini.GeminiNotConfigured):
            gemini.analyze_submission(SOURCE_TEXT, "test", "en")

    chat.assert_not_called()


def test_provider_failure_is_controlled_error(litellm_chat):
    litellm_chat.side_effect = LLMAnalysisError(
        "LiteLLM request failed after 3 attempts (HTTPStatusError)."
    )

    with pytest.raises(
        gemini.GeminiAnalysisError,
        match="after 3 attempts",
    ):
        gemini.analyze_submission(SOURCE_TEXT, "test", "en")


def test_scores_reject_boolean_values():
    payload = extraction_payload(urgency_1_to_5=True)

    with pytest.raises(ValueError):
        ProblemExtraction.model_validate(payload)
