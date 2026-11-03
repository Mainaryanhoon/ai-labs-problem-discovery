from types import SimpleNamespace

from app.commercial_qualification import (
    build_commercial_qualification,
    infer_india_relevance,
)
from app.commercial_scoring import (
    COMMERCIAL_COMPONENT_WEIGHTS,
    COMMERCIAL_WEIGHT_VERSION,
    score_commercial_opportunity_v02,
)


def test_v02_weights_sum_to_100():
    assert sum(COMMERCIAL_COMPONENT_WEIGHTS.values()) == 100
    assert COMMERCIAL_WEIGHT_VERSION == "v0.2-commercial"


def test_v02_missing_evidence_scores_one_not_midpoint():
    result = score_commercial_opportunity_v02(
        demand_analyses=[],
        demand_submissions=[],
        supporting_capability_count=0,
        product_ready=False,
        offering_angle="undetermined",
        qualification={
            "india_relevance": "none",
            "is_commercial_lead": False,
            "buyer_function": None,
            "business_trigger": None,
            "trigger_date": None,
        },
    )
    assert result["active_for_ranking"] is False
    assert result["components"]["commercial_demand_buying"] == 1
    assert result["components"]["timing_market_momentum"] == 1
    assert result["components"]["buyer_identification_path"] == 1
    assert any("no evidence" in line for line in result["limitations"])


def test_india_relevance_requires_authorized_verbatim_role_claim():
    relevance, evidence = infer_india_relevance(
        analyses=[{"problem_statement": "GA4 drift for a US retailer"}],
        submissions=[],
    )
    assert relevance == "none"
    assert evidence is None

    relevance, evidence = infer_india_relevance(
        analyses=[{
            "problem_statement": "GA4 drift at our Bengaluru ecommerce brand",
        }],
        submissions=[],
    )
    assert relevance == "none"
    assert evidence is None

    quote = "Our company operates its analytics team in Bengaluru, India."
    relevance, evidence = infer_india_relevance(
        analyses=[],
        submissions=[SimpleNamespace(text=f"Company note: {quote}")],
        india_evidence_claims=[{
            "role": "india_evidence",
            "evidence_quote": quote,
            "justification": "Company-published location statement.",
            "status": "confirmed",
        }],
    )
    assert relevance == "confirmed"
    assert evidence and quote in evidence

    relevance, evidence = infer_india_relevance(
        analyses=[],
        submissions=[SimpleNamespace(text=quote)],
        india_evidence_claims=[{
            "role": "india_evidence",
            "evidence_quote": "India is mentioned in a summary.",
            "justification": "Untraceable quote.",
            "status": "confirmed",
        }],
    )
    assert relevance == "none"
    assert evidence is None


def test_qualification_does_not_fabricate_company():
    qual = build_commercial_qualification(
        cluster_title="GA4 drift",
        section="Analytics",
        offering_angle="upgrade_existing_offering",
        smallest_first_version="BQ Agent check",
        first_client_type="eCommerce analytics lead",
        failure_risk_line="Native tools",
        demand_analyses=[],
        demand_submissions=[],
        supporting_capability_count=1,
        product_ready=True,
    )
    assert qual["company_name"] is None
    assert qual["buyer_name"] is None
    assert qual["verified_outreach_route"] is None
    assert qual["is_commercial_lead"] is False
    assert qual["is_validated_problem_opportunity"] is True
    assert qual["india_relevance"] == "none"


def test_company_trigger_is_not_a_commercial_lead_and_identity_stays_raw():
    quote = "Acme Ltd expanded operations in Bengaluru on 15 October 2026."
    claims = [{
        "role": "company_trigger",
        "submission_id": 10,
        "company_name": "Acme Ltd",
        "company_identity_resolution": "unresolved",
        "trigger_type": "expansion",
        "event_date_quote": "15 October 2026",
        "publication_date": "2026-10-20T00:00:00+00:00",
        "source_url": "https://example.test/news/1",
        "evidence_quote": quote,
        "justification": "Directly reported event.",
        "status": "confirmed",
        "confidence": 0.9,
    }]
    qual = build_commercial_qualification(
        cluster_title="Analytics modernization",
        section="Analytics",
        offering_angle="upgrade_existing_offering",
        smallest_first_version="Small audit",
        first_client_type="Analytics lead",
        failure_risk_line="No buyer evidence",
        demand_analyses=[],
        demand_submissions=[],
        evidence_submissions=[SimpleNamespace(id=10, text=quote)],
        commercial_claims=claims,
        supporting_capability_count=0,
        product_ready=False,
    )
    assert qual["company_name"] == "Acme Ltd"
    assert qual["company_identity_resolution"] == "unresolved"
    assert qual["business_trigger"]["event_date"] is None
    assert qual["business_trigger"]["event_date_quote"] == "15 October 2026"
    assert qual["is_commercial_lead"] is False


def test_india_evidence_for_different_company_does_not_transfer_to_trigger():
    trigger_quote = "Acme Ltd expanded operations on 15 October 2026."
    india_quote = "OtherCo operates its analytics team in Bengaluru, India."
    claims = [
        {
            "role": "company_trigger",
            "submission_id": 10,
            "company_name": "Acme Ltd",
            "company_identity_resolution": "unresolved",
            "trigger_type": "expansion",
            "event_date_quote": "15 October 2026",
            "event_date": "2026-10-15",
            "publication_date": "2026-10-20T00:00:00+00:00",
            "source_url": "https://example.test/news/1",
            "evidence_quote": trigger_quote,
            "justification": "Directly reported event.",
            "status": "confirmed",
            "confidence": 0.9,
        },
        {
            "role": "india_evidence",
            "submission_id": 11,
            "company_name": "OtherCo",
            "evidence_quote": india_quote,
            "justification": "Company location statement.",
            "status": "confirmed",
        },
    ]
    qual = build_commercial_qualification(
        cluster_title="Analytics modernization",
        section="Analytics",
        offering_angle="upgrade_existing_offering",
        smallest_first_version="Small audit",
        first_client_type="Analytics lead",
        failure_risk_line="No buyer evidence",
        demand_analyses=[],
        demand_submissions=[],
        india_evidence_claims=[claims[1]],
        commercial_claims=claims,
        evidence_submissions=[
            SimpleNamespace(id=10, text=trigger_quote),
            SimpleNamespace(id=11, text=india_quote),
        ],
        supporting_capability_count=0,
        product_ready=False,
    )
    assert qual["company_name"] == "Acme Ltd"
    assert qual["india_relevance"] == "none"


def _commercial_claim(
    role,
    submission_id,
    company="Acme Ltd",
    *,
    status="confirmed",
    quote=None,
):
    return {
        "role": role,
        "submission_id": submission_id,
        "source_item_id": str(submission_id),
        "company_name": company,
        "company_identity_resolution": "unresolved",
        "trigger_type": "expansion" if role == "company_trigger" else None,
        "event_date_quote": "15 October 2026" if role == "company_trigger" else None,
        "event_date": "2026-10-15" if role == "company_trigger" else None,
        "publication_date": f"2026-10-{submission_id:02}T00:00:00+00:00",
        "source_url": f"https://news.example/{submission_id}",
        "evidence_quote": quote or f"{company} evidence item {submission_id}.",
        "justification": "Direct source quote supports this classification.",
        "status": status,
        "confidence": 0.9,
    }


def _commercial_qualification_for_claims(claims, submissions):
    return build_commercial_qualification(
        cluster_title="Measurement opportunity",
        section="Analytics",
        offering_angle="upgrade_existing_offering",
        smallest_first_version="Small measurement audit",
        first_client_type="Analytics lead",
        failure_risk_line="No provider selection evidence",
        demand_analyses=[],
        demand_submissions=[
            submission
            for submission in submissions
            if submission.source_name == "Reddit"
        ],
        india_evidence_claims=[
            claim for claim in claims if claim["role"] == "india_evidence"
        ],
        commercial_claims=claims,
        evidence_submissions=submissions,
        supporting_capability_count=0,
        product_ready=False,
    )


def test_sufficient_exactly_linked_company_evidence_can_qualify():
    india_quote = "Acme Ltd operates its analytics team in Bengaluru, India."
    claims = [
        _commercial_claim("company_trigger", 1, quote=(
            "Acme Ltd expanded operations on 15 October 2026."
        )),
        _commercial_claim("india_evidence", 2, quote=india_quote),
        _commercial_claim("buying_evidence", 3, quote=(
            "Acme Ltd issued an RFP for analytics implementation services."
        )),
    ]
    submissions = [
        SimpleNamespace(
            id=index,
            source_name="Reddit",
            submitted_by="author-a" if index < 6 else "author-b",
            source_url=f"https://reddit.example/{index}",
            text=(
                f"Acme Ltd cannot match GA4 orders, need help with {index}."
            ),
        )
        for index in (4, 5, 6)
    ]
    claims.extend(
        _commercial_claim(
            "demand",
            submission.id,
            quote=f"Acme Ltd cannot match GA4 orders, need help with {submission.id}.",
        )
        for submission in submissions
    )
    submissions.extend([
        SimpleNamespace(
            id=claim["submission_id"],
            source_name="News",
            submitted_by=None,
            source_url=claim["source_url"],
            text=claim["evidence_quote"],
        )
        for claim in claims[:3]
    ])
    qual = _commercial_qualification_for_claims(claims, submissions)
    assert qual["company_identity_resolution"] == "canonical_exact_name"
    assert qual["india_relevance"] == "confirmed"
    assert qual["is_commercial_lead"] is True


def test_similar_company_names_do_not_cross_satisfy_gates():
    claims = [
        _commercial_claim("company_trigger", 1, "Acme Ltd"),
        _commercial_claim("india_evidence", 2, "Acme Laboratories"),
        _commercial_claim("buying_evidence", 3, "Acme Ltd"),
    ]
    submissions = []
    for index in (4, 5, 6):
        company = "Acme Ltd" if index != 6 else "Acme Laboratories"
        text = f"{company} cannot match GA4 orders; need help with {index}."
        claims.append(_commercial_claim("demand", index, company, quote=text))
        submissions.append(SimpleNamespace(
            id=index,
            source_name="Reddit",
            submitted_by=f"author-{index % 2}",
            source_url=f"https://reddit.example/{index}",
            text=text,
        ))
    submissions.extend([
        SimpleNamespace(
            id=claim["submission_id"],
            source_name="News",
            submitted_by=None,
            source_url=claim["source_url"],
            text=claim["evidence_quote"],
        )
        for claim in claims[:3]
    ])
    qual = _commercial_qualification_for_claims(claims, submissions)
    assert qual["is_commercial_lead"] is False


def test_trigger_without_buying_and_buying_without_demand_do_not_qualify():
    trigger = _commercial_claim("company_trigger", 1)
    trigger["evidence_quote"] = "Acme Ltd expanded on 15 October 2026."
    india = _commercial_claim(
        "india_evidence",
        2,
        quote="Acme Ltd operates its analytics team in Bengaluru, India.",
    )
    for claims in (
        [trigger, india],
        [trigger, india, _commercial_claim("buying_evidence", 3)],
    ):
        qual = _commercial_qualification_for_claims(claims, [])
        assert qual["is_commercial_lead"] is False


def test_duplicate_company_demand_claims_do_not_inflate_independence():
    claims = [
        _commercial_claim("company_trigger", 1),
        _commercial_claim("india_evidence", 2, quote=(
            "Acme Ltd operates its analytics team in Bengaluru, India."
        )),
        _commercial_claim("buying_evidence", 3),
    ]
    duplicate = _commercial_claim(
        "demand",
        4,
        quote="Acme Ltd cannot match GA4 orders; need help.",
    )
    claims.extend([duplicate, dict(duplicate), dict(duplicate)])
    submissions = [
        SimpleNamespace(
            id=4,
            source_name="Reddit",
            submitted_by="same-author",
            source_url="https://reddit.example/4",
            text=duplicate["evidence_quote"],
        )
    ]
    submissions.extend([
        SimpleNamespace(
            id=claim["submission_id"],
            source_name="News",
            submitted_by=None,
            source_url=claim["source_url"],
            text=claim["evidence_quote"],
        )
        for claim in claims[:3]
    ])
    qual = _commercial_qualification_for_claims(claims, submissions)
    assert qual["is_commercial_lead"] is False


def test_same_canonical_demand_url_counts_once_for_company_gate():
    claims = [
        _commercial_claim("company_trigger", 1),
        _commercial_claim(
            "india_evidence",
            2,
            quote="Acme Ltd operates its analytics team in Bengaluru, India.",
        ),
        _commercial_claim("buying_evidence", 3),
    ]
    duplicates = []
    submissions = []
    for submission_id, author in ((4, "a"), (5, "b"), (6, "c")):
        quote = f"Acme Ltd cannot match GA4 orders, need help {submission_id}."
        claim = _commercial_claim("demand", submission_id, quote=quote)
        claim["source_url"] = "https://reddit.example/reposted"
        claim["canonical_url"] = "https://reddit.example/reposted"
        duplicates.append(claim)
        submissions.append(SimpleNamespace(
            id=submission_id,
            source_name="Reddit",
            submitted_by=author,
            source_url="https://reddit.example/reposted",
            text=quote,
        ))
    claims.extend(duplicates)
    submissions.extend([
        SimpleNamespace(
            id=claim["submission_id"],
            source_name="News",
            submitted_by=None,
            source_url=claim["source_url"],
            text=claim["evidence_quote"],
        )
        for claim in claims[:3]
    ])
    qual = _commercial_qualification_for_claims(claims, submissions)
    assert qual["is_commercial_lead"] is False


def test_repeated_demand_quote_across_urls_counts_once():
    claims = [
        _commercial_claim("company_trigger", 1),
        _commercial_claim(
            "india_evidence",
            2,
            quote="Acme Ltd operates its analytics team in Bengaluru, India.",
        ),
        _commercial_claim("buying_evidence", 3),
    ]
    quote = "Acme Ltd cannot match GA4 orders; need help with reconciliation."
    submissions = []
    for submission_id, author in ((4, "author-a"), (5, "author-b"), (6, "author-c")):
        claim = _commercial_claim("demand", submission_id, quote=quote)
        claim["canonical_url"] = f"https://reddit.example/{submission_id}"
        claim["source_url"] = claim["canonical_url"]
        claims.append(claim)
        submissions.append(SimpleNamespace(
            id=submission_id,
            source_name="Reddit",
            submitted_by=author,
            source_url=claim["source_url"],
            text=quote,
        ))
    submissions.extend([
        SimpleNamespace(
            id=claim["submission_id"],
            source_name="News",
            submitted_by=None,
            source_url=claim["source_url"],
            text=claim["evidence_quote"],
        )
        for claim in claims[:3]
    ])
    qual = _commercial_qualification_for_claims(claims, submissions)
    assert qual["is_commercial_lead"] is False


def test_ambiguous_company_identity_stays_unresolved_and_fails_gate():
    claims = [
        _commercial_claim("company_trigger", 1, company="Retailer"),
        _commercial_claim("india_evidence", 2, company="Retailer", quote="Retailer operates in Bengaluru, India."),
        _commercial_claim("buying_evidence", 3, company="Retailer"),
    ]
    submissions = [
        SimpleNamespace(
            id=1,
            source_name="News",
            submitted_by=None,
            source_url="https://news.example/1",
            text="Retailer expanded on 15 October 2026.",
        ),
        SimpleNamespace(
            id=2,
            source_name="News",
            submitted_by=None,
            source_url="https://news.example/2",
            text="Retailer operates in Bengaluru, India.",
        ),
        SimpleNamespace(
            id=3,
            source_name="News",
            submitted_by=None,
            source_url="https://news.example/3",
            text="Retailer issued an RFP for analytics.",
        ),
    ]
    qual = _commercial_qualification_for_claims(claims, submissions)
    assert qual["company_identity_resolution"] == "unresolved"
    assert qual["qualification_gates"]["company_identity_resolution"]["passed"] is False
    assert "Generic descriptor" in qual["qualification_gates"]["company_identity_resolution"]["reason"]
    assert qual["is_commercial_lead"] is False


def test_plausible_unverified_india_relevance_records_uncertainty_and_fails_gate():
    trigger = _commercial_claim("company_trigger", 1, "Acme Ltd")
    trigger["event_date"] = "2026-10-15"
    trigger["event_date_quote"] = "15 October 2026"
    trigger["evidence_quote"] = "Acme Ltd expanded operations on 15 October 2026."

    india_unverified = _commercial_claim(
        "india_evidence",
        2,
        "Acme Ltd",
        status="plausible_unverified",
        quote="Acme Ltd ran an India marketing initiative.",
    )
    buying = _commercial_claim("buying_evidence", 3, "Acme Ltd")
    buying["evidence_quote"] = "Acme Ltd issued an RFP for analytics implementation."

    claims = [trigger, india_unverified, buying]
    submissions = [
        SimpleNamespace(
            id=claim["submission_id"],
            source_name="News",
            submitted_by=None,
            source_url=claim["source_url"],
            text=claim["evidence_quote"],
        )
        for claim in claims
    ]
    qual = _commercial_qualification_for_claims(claims, submissions)
    assert qual["india_relevance"] == "plausible_unverified"
    assert qual["qualification_gates"]["india_presence"]["passed"] is False
    assert qual["qualification_gates"]["india_presence"]["uncertainty"] is not None
    assert qual["is_commercial_lead"] is False


def test_qualification_gates_structure_citations_and_dates():
    india_quote = "Acme Ltd operates its analytics team in Bengaluru, India."
    trigger_quote = "Acme Ltd expanded operations on 15 October 2026."
    buying_quote = "Acme Ltd issued an RFP for analytics implementation services."

    claims = [
        _commercial_claim("company_trigger", 1, quote=trigger_quote),
        _commercial_claim("india_evidence", 2, quote=india_quote),
        _commercial_claim("buying_evidence", 3, quote=buying_quote),
    ]
    claims[0]["event_date"] = "2026-10-15"
    claims[0]["event_date_quote"] = "15 October 2026"

    submissions = [
        SimpleNamespace(
            id=index,
            source_name="Reddit",
            submitted_by="author-a" if index < 6 else "author-b",
            source_url=f"https://reddit.example/{index}",
            text=f"Acme Ltd cannot match GA4 orders, need help with {index}.",
        )
        for index in (4, 5, 6)
    ]
    claims.extend(
        _commercial_claim(
            "demand",
            submission.id,
            quote=f"Acme Ltd cannot match GA4 orders, need help with {submission.id}.",
        )
        for submission in submissions
    )
    submissions.extend([
        SimpleNamespace(
            id=claim["submission_id"],
            source_name="News",
            submitted_by=None,
            source_url=claim["source_url"],
            text=claim["evidence_quote"],
        )
        for claim in claims[:3]
    ])
    qual = _commercial_qualification_for_claims(claims, submissions)
    gates = qual["qualification_gates"]
    assert gates["company_trigger"]["passed"] is True
    assert gates["company_trigger"]["status"] == "PASSED"
    assert gates["company_trigger"]["citations"][0]["event_date"] == "2026-10-15"
    assert gates["india_presence"]["passed"] is True
    assert gates["india_presence"]["status"] == "PASSED"
    assert gates["buying_evidence"]["passed"] is True
    assert gates["buying_evidence"]["status"] == "PASSED"
    assert gates["company_identity_resolution"]["passed"] is True
    assert gates["company_specific_demand"]["passed"] is True
    assert qual["is_commercial_lead"] is True

