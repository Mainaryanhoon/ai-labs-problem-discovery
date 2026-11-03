"""Source-role demand floor: HN KEEP_DEMAND must not inflate eligibility."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import build_opportunity_summary
from app.models import (
    ClusterEvidence,
    ProblemCluster,
    SourceObservation,
    Submission,
)
from app.source_roles import is_demand_role, source_role
from app.source_roles import (
    EVIDENCE_ROLES,
    build_role_claim,
    is_demand_role,
    is_market_lead_role,
    is_valid_evidence_role,
    source_role,
)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _analysis(**overrides):
    payload = {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "problem_statement": "GA4 purchases do not match store orders weekly.",
        "urgency_1_to_5": 4,
        "frequency_signal_1_to_5": 4,
        "evidence_strength_1_to_5": 4,
        "willingness_to_pay_signal": "suggested",
        "software_buildability": "likely",
        "evidence_quotes": ["reconcile weekly by hand"],
        "section": "Analytics",
        "filter_reason_code": "KEEP_DEMAND",
        "capability_readiness_1_to_5": 4,
        "section_client_fit_1_to_5": 5,
        "leverage_on_offerings_1_to_5": 5,
        "commercial_signal_1_to_5": 3,
        "effort_to_first_version_1_to_5": 4,
        "defensibility_1_to_5": 3,
        "smallest_first_version": "BQ Agent check for GA4 vs order mismatch",
        "first_client_type": "eCommerce analytics lead",
        "failure_risk_line": "Native GA4 tools may shrink demand",
        "affected_user": "eCommerce analytics lead",
    }
    payload.update(overrides)
    return payload


def _add(db, cluster_id, *, source, author, url_suffix, code="KEEP_DEMAND"):
    # Unique body per URL so exact-text dedupe does not collapse independent items.
    quote = f"reconcile weekly by hand for case {url_suffix}"
    text = (
        f"GA4 purchases don't match store orders on case {url_suffix}; "
        f"we {quote}. Need Tag Auditor / BQ Agent automation for {author}."
    )
    submission = Submission(
        text=text,
        source_name=source,
        source_url=f"https://example.test/{url_suffix}",
        submitted_by=author,
        analysis_status="completed",
        analysis_json=json.dumps(_analysis(
            filter_reason_code=code,
            evidence_quotes=[quote],
            problem_statement=(
                f"GA4 purchases do not match store orders weekly ({url_suffix})."
            ),
        )),
    )
    db.add(submission)
    db.flush()
    db.add(ClusterEvidence(
        cluster_id=cluster_id,
        submission_id=submission.id,
        is_independent=True,
        relationship_type="supports",
    ))
    return submission


def test_source_role_mapping():
    assert source_role("Hacker News") == "capability_corroboration"
    assert source_role("hacker_news") == "capability_corroboration"
    assert is_demand_role("Reddit")
    assert is_demand_role("Stack Exchange:stackoverflow")
    assert not is_demand_role("Hacker News")
    assert source_role("mystery_forum") == "unknown"
    assert not is_demand_role("mystery_forum")
    assert source_role("X") == "idea"
    assert source_role("YouTube") == "opt_in_context"


def test_market_and_idea_feeds_count_as_market_lead_roles():
    assert not is_market_lead_role("Hacker News")
    assert not is_demand_role("articles")
    assert is_market_lead_role(
        "articles",
        "https://www.medianama.com/feed/",
    )
    assert is_market_lead_role(
        "articles",
        "https://analyticsindiamag.com/feed/",
    )
    claim = build_role_claim(
        "articles",
        "idea",
        source_text="Indian D2C founders still hire analysts to rebuild GST reports.",
        evidence_quote="Indian D2C founders still hire analysts to rebuild GST reports.",
        justification="Founder-news feed is annotated as market/idea.",
        policy_url="https://www.medianama.com/feed/",
    )
    assert claim and claim["role"] == "idea"


def test_role_claims_require_policy_permission_and_verbatim_evidence():
    quote = "GA4 release adds a new reporting capability"
    assert EVIDENCE_ROLES == {
        "demand",
        "capability",
        "context",
        "company_trigger",
        "india_evidence",
        "buying_evidence",
        "buyer_route",
        "market",
        "idea",
    }
    capability = build_role_claim(
        "official_docs",
        "capability",
        source_text=quote,
        evidence_quote=quote,
        justification="Official release notes describe a product capability.",
    )
    assert capability and capability["role"] == "capability"
    assert all(is_valid_evidence_role(role) for role in EVIDENCE_ROLES)
    assert not is_valid_evidence_role("unknown")
    demand = build_role_claim(
        "reddit",
        "demand",
        source_text=quote,
        evidence_quote=quote,
        justification="Approved source and verbatim problem text.",
    )
    assert demand and demand["role"] == "demand"
    assert source_role("future-source") == "unknown"
    assert not is_demand_role("future-source")
    assert build_role_claim(
        "Hacker News",
        "demand",
        source_text=quote,
        evidence_quote=quote,
        justification="Relevant keyword hit.",
    ) is None
    assert build_role_claim(
        "unknown source",
        "company_trigger",
        source_text=quote,
        evidence_quote=quote,
        justification="No policy entry exists.",
    ) is None
    assert build_role_claim(
        "youtube",
        "context",
        source_text=quote,
        evidence_quote="not a source substring",
        justification="Title was summarized.",
    ) is None
    assert build_role_claim(
        "youtube",
        "unknown",
        source_text=quote,
        evidence_quote=quote,
        justification="Unknown roles fail closed.",
    ) is None


def test_hn_keep_demand_does_not_count_toward_demand_floor():
    db = _session()
    try:
        cluster = ProblemCluster(
            title="GA4 drift",
            problem_statement="GA4 drift",
            category="Software & IT",
            status="candidate",
        )
        db.add(cluster)
        db.flush()
        _add(db, cluster.id, source="Reddit", author="a1", url_suffix="r1")
        _add(
            db,
            cluster.id,
            source="Stack Exchange:stackoverflow",
            author="a2",
            url_suffix="se1",
        )
        _add(
            db,
            cluster.id,
            source="Hacker News",
            author="a3",
            url_suffix="hn1",
            code="KEEP_DEMAND",
        )
        db.commit()

        summary = build_opportunity_summary(cluster.id, db)
        assert summary["eligible_evidence_count"] == 2
        assert summary["demand_metrics"]["demand_role_item_count"] == 2
        assert summary["supporting_capability_evidence_count"] == 1
        assert summary["role_blocked_keep_demand"]
        assert summary["product_ready"] is False
        assert summary["top10_eligible"] is False
    finally:
        db.close()


def test_two_demand_plus_hn_fails_three_demand_minimum():
    db = _session()
    try:
        cluster = ProblemCluster(
            title="GA4 drift two",
            problem_statement="GA4 drift",
            category="Software & IT",
            status="candidate",
        )
        db.add(cluster)
        db.flush()
        _add(db, cluster.id, source="Reddit", author="a1", url_suffix="r2")
        _add(
            db,
            cluster.id,
            source="Stack Exchange:stackoverflow",
            author="a2",
            url_suffix="se2",
        )
        _add(db, cluster.id, source="Hacker News", author="a3", url_suffix="hn2")
        db.commit()
        summary = build_opportunity_summary(cluster.id, db)
        assert summary["demand_metrics"]["item_count"] == 2
        assert summary["product_ready"] is False
    finally:
        db.close()


def test_three_demand_role_items_can_pass_floor():
    db = _session()
    try:
        cluster = ProblemCluster(
            title="GA4 drift three",
            problem_statement="GA4 drift",
            category="Software & IT",
            status="candidate",
        )
        db.add(cluster)
        db.flush()
        _add(db, cluster.id, source="Reddit", author="a1", url_suffix="r3")
        _add(
            db,
            cluster.id,
            source="Stack Exchange:stackoverflow",
            author="a2",
            url_suffix="se3",
        )
        _add(
            db,
            cluster.id,
            source="Stack Exchange:datascience",
            author="a3",
            url_suffix="ds3",
        )
        # Capability corroboration must not be required for readiness.
        _add(
            db,
            cluster.id,
            source="Hacker News",
            author="a4",
            url_suffix="hn3",
            code="KEEP_CAPABILITY",
        )
        db.commit()
        summary = build_opportunity_summary(cluster.id, db)
        assert summary["eligible_evidence_count"] == 3
        assert summary["supporting_capability_evidence_count"] == 1
        assert summary["product_ready"] is True
        assert summary["score"] is not None
        assert summary["weight_version"] == "v0.1"
        assert summary["commercial_score_v02"]["weight_version"] == "v0.2-commercial"
        assert summary["commercial_score_v02"]["active_for_ranking"] is False
        assert summary["commercial_qualification"]["is_commercial_lead"] is False
        assert summary["commercial_qualification"]["is_validated_problem_opportunity"] is True
    finally:
        db.close()


def test_unknown_source_role_does_not_count_as_demand():
    db = _session()
    try:
        cluster = ProblemCluster(
            title="unknown source cluster",
            problem_statement="GA4 drift",
            category="Software & IT",
            status="candidate",
        )
        db.add(cluster)
        db.flush()
        for index, author in enumerate(("a1", "a2", "a3")):
            _add(
                db,
                cluster.id,
                source="MysteryForum",
                author=author,
                url_suffix=f"m{index}",
            )
        db.commit()
        summary = build_opportunity_summary(cluster.id, db)
        assert summary["eligible_evidence_count"] == 0
        assert summary["product_ready"] is False
        assert len(summary["role_blocked_keep_demand"]) == 3
    finally:
        db.close()


def test_secondary_roles_on_one_demand_item_do_not_multiply_demand_count():
    db = _session()
    try:
        cluster = ProblemCluster(
            title="one multi-role item",
            problem_statement="GA4 drift",
            category="Software & IT",
            status="candidate",
        )
        db.add(cluster)
        db.flush()
        submission = _add(
            db,
            cluster.id,
            source="Reddit",
            author="one",
            url_suffix="multi",
        )
        db.add(SourceObservation(
            submission_id=submission.id,
            source_name="Reddit",
            source_url=submission.source_url,
            canonical_url=submission.source_url,
            native_id="multi",
            community="analytics",
            metadata_json=json.dumps({
                "evidence_roles": [
                    {
                        "role": "demand",
                        "evidence_quote": "reconcile weekly by hand for case multi",
                        "justification": "Approved source and verbatim problem text.",
                    },
                    {
                        "role": "company_trigger",
                        "evidence_quote": "reconcile weekly by hand for case multi",
                        "justification": "A secondary role on the same item.",
                    },
                ]
            }),
        ))
        db.commit()
        summary = build_opportunity_summary(cluster.id, db)
        assert summary["demand_metrics"]["demand_role_item_count"] == 1
        assert summary["demand_metrics"]["item_count"] == 1
    finally:
        db.close()


def test_forum_demand_requires_policy_and_validated_item_claim(monkeypatch):
    from app import source_policy

    feed = "https://discuss.google.dev/c/google-cloud/data-analytics/86.rss"
    forum_name = "Google Developer Forums"

    def install_policy(roles):
        source_policy.load_source_policy.cache_clear()
        monkeypatch.setattr(
            source_policy,
            "_read_sources_text",
            lambda: (
                "## 5. Articles and blogs\n"
                f"- {feed} (roles: {', '.join(roles)})\n"
            ),
        )

    def forum_cluster(policy_roles):
        install_policy(policy_roles)
        db = _session()
        cluster = ProblemCluster(
            title="Forum demand gate",
            problem_statement="Unresolved BigQuery permissions issue",
            category="Software & IT",
        )
        db.add(cluster)
        db.flush()
        submission = _add(
            db,
            cluster.id,
            source=forum_name,
            author="forum-user",
            url_suffix=f"forum-{','.join(policy_roles)}",
        )
        quote = (
            "GA4 purchases don't match store orders on case "
            f"forum-{','.join(policy_roles)}"
        )
        db.add(SourceObservation(
            submission_id=submission.id,
            source_name=forum_name,
            source_url=submission.source_url,
            canonical_url=submission.source_url,
            native_id="forum-native-id",
            metadata_json=json.dumps({
                "feed": feed,
                "commercial_evidence_claims": [{
                    "role": "demand",
                    "evidence_quote": quote,
                    "justification": "Question describes an unresolved manual issue.",
                    "confidence": 0.9,
                    "status": "confirmed",
                    "company_name": None,
                    "trigger_type": None,
                    "event_date_quote": None,
                    "route": None,
                    "evidence_source": None,
                }],
            }),
            source_created_at=datetime.now(timezone.utc),
        ))
        db.commit()
        summary = build_opportunity_summary(cluster.id, db)
        count = summary["demand_metrics"]["demand_role_item_count"]
        db.close()
        return count

    try:
        assert forum_cluster(["context"]) == 0
        assert forum_cluster(["context", "demand"]) == 1
    finally:
        source_policy.load_source_policy.cache_clear()


def test_duplicate_authors_do_not_inflate_independence_for_product_ready():
    db = _session()
    try:
        cluster = ProblemCluster(
            title="dup authors",
            problem_statement="GA4 drift",
            category="Software & IT",
            status="candidate",
        )
        db.add(cluster)
        db.flush()
        _add(db, cluster.id, source="Reddit", author="same", url_suffix="d1")
        _add(
            db,
            cluster.id,
            source="Stack Exchange:stackoverflow",
            author="same",
            url_suffix="d2",
        )
        _add(
            db,
            cluster.id,
            source="Stack Exchange:datascience",
            author="same",
            url_suffix="d3",
        )
        db.commit()
        summary = build_opportunity_summary(cluster.id, db)
        assert summary["demand_metrics"]["distinct_authors"] == 1
        assert summary["top10_eligible"] is False
        assert summary["product_ready"] is False
    finally:
        db.close()
