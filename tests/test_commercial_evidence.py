import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import source_policy
from app.commercial_evidence import validate_evidence_claims
from app.db import Base
from app.models import SourceObservation, Submission
from app.schemas import ProblemExtraction
from app.services.analysis import analyze_pending_batch


FEED = "https://news.example/feed"
PUBLISHED = "2026-10-28T09:30:00+00:00"


def _proposal(role, quote, **overrides):
    return {
        "role": role,
        "evidence_quote": quote,
        "justification": "The quoted source text directly supports this claim.",
        "confidence": 0.8,
        "status": "confirmed",
        **overrides,
    }


def _policy(monkeypatch, roles):
    text = f"## 5. Articles and blogs\n- {FEED} (roles: {', '.join(roles)})\n"
    monkeypatch.setattr(source_policy, "_read_sources_text", lambda: text)


def _validate(proposals, text, *, policy_url=FEED, source_name="articles", context=None):
    return validate_evidence_claims(
        proposals,
        source_name=source_name,
        source_text=text,
        policy_url=policy_url,
        source_item_id=42,
        submission_id=99,
        source_url="https://news.example/story/1",
        canonical_url="https://news.example/story/1",
        publication_date=PUBLISHED,
        evidence_context=context,
    )


def test_company_trigger_is_traceable_separate_from_buying(monkeypatch):
    _policy(monkeypatch, ["company_trigger"])
    quote = "Acme Ltd announced expansion in Bengaluru on 15 October 2026."
    proposal = _proposal(
        "company_trigger",
        quote,
        company_name="Acme Ltd",
        trigger_type="expansion",
        event_date_quote="15 October 2026",
        event_date="2099-01-01",
        source_item_id=999,
    )
    claims, diagnostics = _validate([proposal], quote)
    assert diagnostics["accepted"] == 1
    claim = claims[0]
    assert claim["source_item_id"] == 42
    assert claim["canonical_url"] == "https://news.example/story/1"
    assert claim["publication_date"] == PUBLISHED
    assert claim["event_date_quote"] == "15 October 2026"
    assert claim["event_date"] == "2026-10-15"
    assert claim["company_name"] == "Acme Ltd"
    assert claim["company_identity_resolution"] == "unresolved"
    assert "buying_evidence" not in {item["role"] for item in claims}


def test_low_confidence_confirmed_claim_is_preserved_as_uncertain(monkeypatch):
    _policy(monkeypatch, ["company_trigger"])
    quote = "Acme Ltd expanded operations on 15 October 2026."
    claims, _ = _validate([_proposal(
        "company_trigger",
        quote,
        confidence=0.5,
        company_name="Acme Ltd",
        trigger_type="expansion",
        event_date_quote="15 October 2026",
    )], quote)
    assert claims[0]["status"] == "plausible_unverified"


def test_company_trigger_rejects_missing_date_or_ambiguous_company(monkeypatch):
    _policy(monkeypatch, ["company_trigger"])
    quote = "Acme announced expansion in India."
    claims, diagnostics = _validate([_proposal(
        "company_trigger",
        quote,
        company_name="Acme Holdings",
        trigger_type="expansion",
        event_date_quote="",
    )], quote)
    assert claims == []
    assert diagnostics["rejected"][0]["reason"] == "company_name_not_supported_by_quote"


def test_unparseable_event_date_remains_uncertain(monkeypatch):
    _policy(monkeypatch, ["company_trigger"])
    quote = "Acme Ltd expanded operations during Q3 2026."
    claims, _ = _validate([_proposal(
        "company_trigger",
        quote,
        company_name="Acme Ltd",
        trigger_type="expansion",
        event_date_quote="2026",
    )], quote)
    assert claims[0]["event_date"] is None
    assert claims[0]["status"] == "plausible_unverified"


def test_exact_quote_and_india_evidence_are_required(monkeypatch):
    _policy(monkeypatch, ["india_evidence"])
    text = "Acme Ltd has a regional operations centre."
    claim, diagnostics = _validate([
        _proposal(
            "india_evidence",
            "Acme Ltd operates in India.",
            company_name="Acme Ltd",
        )
    ], text)
    assert claim == []
    assert diagnostics["rejected"][0]["reason"] == "quote_not_in_stored_text"

    quote = "Acme Ltd ran an India-focused marketing campaign."
    claims, _ = _validate([_proposal(
        "india_evidence",
        quote,
        company_name="Acme Ltd",
    )], quote)
    assert claims[0]["status"] == "plausible_unverified"

    direct_quote = "Acme Ltd operates its analytics team in Bengaluru, India."
    direct, _ = _validate([_proposal(
        "india_evidence",
        direct_quote,
        company_name="Acme Ltd",
    )], direct_quote)
    assert direct[0]["status"] == "confirmed"


def test_funding_and_hiring_do_not_become_buying_evidence(monkeypatch):
    _policy(monkeypatch, ["buying_evidence"])
    for quote in (
        "Acme Ltd raised $20 million in Series B funding.",
        "Acme Ltd is hiring a measurement analyst in Bengaluru.",
        "Acme Ltd launched a new analytics platform.",
    ):
        claims, diagnostics = _validate([_proposal(
            "buying_evidence",
            quote,
            company_name="Acme Ltd",
        )], quote)
        assert claims == []
        assert diagnostics["rejected"][0]["reason"] == (
            "explicit_procurement_or_provider_search_missing"
        )


def test_explicit_provider_search_can_be_buying_evidence(monkeypatch):
    _policy(monkeypatch, ["buying_evidence"])
    quote = "Acme Ltd issued an RFP for analytics implementation services."
    claims, _ = _validate([_proposal(
        "buying_evidence",
        quote,
        company_name="Acme Ltd",
    )], quote)
    assert claims[0]["role"] == "buying_evidence"

    uncertain, diagnostics = _validate([_proposal(
        "buying_evidence",
        quote,
        company_name="Acme Ltd",
        status="plausible_unverified",
    )], quote)
    assert uncertain == []
    assert diagnostics["rejected"][0]["reason"] == "explicit_claim_not_confirmed"


def test_buyer_route_requires_published_purposeful_contact(monkeypatch):
    _policy(monkeypatch, ["buyer_route"])
    quote = "Acme Ltd CEO Mira Rao attended the industry summit."
    claims, diagnostics = _validate([_proposal(
        "buyer_route",
        quote,
        company_name="Acme Ltd",
        route="mira@acme.example",
    )], quote)
    assert claims == []
    assert diagnostics["rejected"][0]["reason"] == "public_purposeful_route_not_verified"

    quote = "Acme Ltd CEO: for contact inquiries write mira.rao@acme.example."
    claims, diagnostics = _validate([_proposal(
        "buyer_route",
        quote,
        company_name="Acme Ltd",
        route="mira.rao@acme.example",
    )], quote)
    assert claims == []
    assert diagnostics["rejected"][0]["reason"] == "public_purposeful_route_not_verified"

    quote = "Acme Ltd: for sales inquiries contact analytics-sales@acme.example."
    claims, _ = _validate([_proposal(
        "buyer_route",
        quote,
        company_name="Acme Ltd",
        route="analytics-sales@acme.example",
    )], quote)
    assert claims[0]["route"] == "analytics-sales@acme.example"

    uncertain, diagnostics = _validate([_proposal(
        "buyer_route",
        quote,
        company_name="Acme Ltd",
        route="analytics-sales@acme.example",
        status="plausible_unverified",
    )], quote)
    assert uncertain == []
    assert diagnostics["rejected"][0]["reason"] == "explicit_claim_not_confirmed"


def test_forum_is_context_unless_exact_policy_url_permits_demand(monkeypatch):
    forum = "https://discuss.google.dev/c/google-cloud/data-analytics/86.rss"
    text = f"## 5. Articles and blogs\n- {forum} (roles: context)\n"
    monkeypatch.setattr(source_policy, "_read_sources_text", lambda: text)
    quote = "I cannot query BigQuery; how can I resolve this permission error?"
    claim, diagnostics = validate_evidence_claims(
        [
            _proposal("context", quote),
            _proposal("demand", quote),
        ],
        source_name="articles",
        source_text=quote,
        policy_url=forum,
        source_item_id=3,
        submission_id=3,
        source_url="https://discuss.google.dev/t/example/1",
        canonical_url="https://discuss.google.dev/t/example/1",
        publication_date=PUBLISHED,
        analysis_classification={
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "filter_reason_code": "KEEP_DEMAND",
        },
    )
    assert [item["role"] for item in claim] == ["context"]
    assert diagnostics["rejected"][0]["reason"] == "role_not_permitted_or_reason_missing"

    text = f"## 5. Articles and blogs\n- {forum} (roles: context, demand)\n"
    monkeypatch.setattr(source_policy, "_read_sources_text", lambda: text)
    promoted, _ = validate_evidence_claims(
        [_proposal("demand", quote)],
        source_name="articles",
        source_text=quote,
        policy_url=forum,
        source_item_id=3,
        submission_id=3,
        source_url="https://discuss.google.dev/t/example/1",
        canonical_url="https://discuss.google.dev/t/example/1",
        publication_date=PUBLISHED,
        analysis_classification={
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "filter_reason_code": "KEEP_DEMAND",
        },
    )
    assert promoted[0]["role"] == "demand"

    answered = "The accepted answer is to recreate the BigQuery dataset."
    rejected, diagnostics = validate_evidence_claims(
        [_proposal("demand", answered)],
        source_name="articles",
        source_text=answered,
        policy_url=forum,
        source_item_id=4,
        submission_id=4,
        source_url="https://discuss.google.dev/t/example/2",
        canonical_url="https://discuss.google.dev/t/example/2",
        publication_date=PUBLISHED,
        analysis_classification={
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "filter_reason_code": "KEEP_DEMAND",
        },
    )
    assert rejected == []
    assert diagnostics["rejected"][0]["reason"] == (
        "genuine_problem_statement_not_supported"
    )


def test_video_quotes_must_match_declared_available_origin():
    title = "BigQuery product demonstration"
    text = f"{title}\n\nDescription text."
    proposal = _proposal(
        "capability",
        title,
        evidence_source="transcript",
    )
    claims, diagnostics = _validate(
        [proposal],
        text,
        source_name="youtube",
        policy_url=None,
        context={"title": title, "description": "Description text."},
    )
    assert claims == []
    assert diagnostics["rejected"][0]["reason"] == (
        "video_quote_source_unavailable_or_mismatched"
    )

    proposal["evidence_source"] = "title"
    claims, _ = _validate(
        [proposal],
        text,
        source_name="youtube",
        policy_url=None,
        context={"title": title, "description": "Description text."},
    )
    assert claims[0]["evidence_source"] == "title"


def test_duplicate_claims_are_deduplicated_with_diagnostics(monkeypatch):
    _policy(monkeypatch, ["company_trigger"])
    quote = "Acme Ltd expanded operations on 15 October 2026."
    proposal = _proposal(
        "company_trigger",
        quote,
        company_name="Acme Ltd",
        trigger_type="expansion",
        event_date_quote="15 October 2026",
    )
    claims, diagnostics = _validate([proposal, proposal], quote)
    assert len(claims) == 1
    assert diagnostics["duplicate_claims"] == 1


def test_malformed_claim_shapes_fail_closed_with_diagnostics():
    claims, diagnostics = _validate(
        [None, "not-an-object", {"role": "not-a-role"}],
        "Stored article text with enough content.",
    )
    assert claims == []
    assert [item["reason"] for item in diagnostics["rejected"]] == [
        "malformed_claim_shape",
        "malformed_claim_shape",
        "unknown_role",
    ]


def test_analysis_persists_only_validated_claims_and_diagnostics(monkeypatch):
    _policy(monkeypatch, ["company_trigger"])
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    quote = "Acme Ltd expanded operations on 15 October 2026."
    with Session() as db:
        submission = Submission(
            text=quote,
            source_name="articles",
            source_url="https://news.example/story/1",
            analysis_status="pending",
        )
        db.add(submission)
        db.flush()
        db.add(SourceObservation(
            submission_id=submission.id,
            source_name="articles",
            source_url=submission.source_url,
            canonical_url=submission.source_url,
            native_id="article-1",
            source_created_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
            metadata_json=json.dumps({"feed": FEED}),
        ))
        db.commit()

        def analyzer(_text, _source, _language, *, source_context=None):
            context = json.loads(source_context)
            assert context["evidence_claim_policy"]["permitted_roles"] == [
                "company_trigger"
            ]
            payload = {
                "is_genuine_problem": False,
                "signal_type": "general_discussion",
                "rejection_reason": "Company announcement, not practitioner pain.",
                "problem_statement": "No practitioner problem stated.",
                "affected_user": "Unclear",
                "context": "Company news item.",
                "category": "Other",
                "industry": "Unclear",
                "urgency_1_to_5": 1,
                "frequency_signal_1_to_5": 1,
                "evidence_strength_1_to_5": 1,
                "evidence_claims": [
                    _proposal(
                        "company_trigger",
                        quote,
                        company_name="Acme Ltd",
                        trigger_type="expansion",
                        event_date_quote="15 October 2026",
                    ),
                    _proposal(
                        "buying_evidence",
                        quote,
                        company_name="Acme Ltd",
                    ),
                ],
            }
            return ProblemExtraction.model_validate(payload), "offline-test"

        result = analyze_pending_batch(db, 1, analyzer=analyzer)
        assert result["completed"] == 1
        assert result["items"][0]["commercial_claim_diagnostics"]["accepted"] == 1
        observation = db.scalars(select(SourceObservation)).one()
        metadata = json.loads(observation.metadata_json)
        assert metadata["commercial_evidence_claims"][0]["role"] == "company_trigger"
        assert metadata["commercial_evidence_claims"][0]["submission_id"] == submission.id
        assert metadata["commercial_claim_diagnostics"]["rejected"][0]["reason"] == (
            "role_not_permitted_or_reason_missing"
        )
        stored_analysis = json.loads(submission.analysis_json)
        assert stored_analysis["evidence_claims"] == []
    engine.dispose()