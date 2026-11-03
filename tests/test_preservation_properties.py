"""Preservation property tests — Task 2 (bugfix spec: pipeline-opportunity-surfacing-bug).

These tests capture non-buggy baseline behavior that MUST remain intact after any fix.
All tests MUST PASS on the current UNFIXED code.

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.7
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import build_opportunity_summary
from app.models import ClusterEvidence, ProblemCluster, Submission
from app.tatvic_scoring import (
    KNOWN_OFFERING_MARKERS,
    _offering_angle,
    cites_known_offering,
    is_product_ready,
    score_tatvic_opportunity,
)


# ---------------------------------------------------------------------------
# Shared DB fixture (mirrors tests/test_opportunity_scoring.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def cluster(db):
    item = ProblemCluster(
        title="GA4 purchase reconciliation drift",
        problem_statement=(
            "GA4 purchases do not match store orders; BQ Agent check needed."
        ),
        category="Software & IT",
        status="candidate",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _analysis(**overrides):
    payload = {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "problem_statement": (
            "GA4 purchases do not match store orders weekly. "
            "Need GA4 / BQ Agent reconciliation."
        ),
        "urgency_1_to_5": 4,
        "frequency_signal_1_to_5": 4,
        "evidence_strength_1_to_5": 4,
        "willingness_to_pay_signal": "suggested",
        "software_buildability": "likely",
        "evidence_quotes": ["reconcile weekly by hand"],
        "section": "Analytics",
        "filter_reason_code": "KEEP_DEMAND",
        "capability_readiness_1_to_5": 4,
        "section_client_fit_1_to_5": 4,
        "leverage_on_offerings_1_to_5": 4,
        "commercial_signal_1_to_5": 3,
        "effort_to_first_version_1_to_5": 3,
        "defensibility_1_to_5": 2,
        "smallest_first_version": "BQ Agent check for GA4 vs order mismatch",
        "first_client_type": "eCommerce analytics lead",
        "failure_risk_line": "Native GA4 tools may shrink demand",
        "affected_user": "eCommerce analytics lead",
    }
    payload.update(overrides)
    return payload


def _add_submission(db, cluster_id, *, source, author, url_suffix, code="KEEP_DEMAND"):
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
        language="en",
        analysis_status="completed",
        analysis_json=json.dumps(_analysis(
            filter_reason_code=code,
            evidence_quotes=[quote],
            problem_statement=(
                f"GA4 purchases do not match store orders weekly ({url_suffix}). "
                "Need GA4 / BQ Agent reconciliation."
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
    db.commit()
    return submission


# ---------------------------------------------------------------------------
# Req 3.1 — Demand source preservation
# KEEP_DEMAND items from demand-role sources must count toward demand floor.
# Validates: Requirements 3.1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source_name", [
    "Reddit",
    "Stack Exchange:stackoverflow",
    "Stack Exchange:datascience",
])
def test_demand_role_source_counts_toward_demand_floor(db, cluster, source_name):
    """Req 3.1: KEEP_DEMAND items from approved demand-role sources count.

    **Validates: Requirements 3.1**
    """
    # Add three KEEP_DEMAND items from the parameterized demand-role source so
    # that the cluster can reach the demand-floor minimum.
    for idx in range(3):
        _add_submission(
            db,
            cluster.id,
            source=source_name,
            author=f"author_{idx}",
            url_suffix=f"{source_name.replace(':', '_')}_{idx}",
        )

    summary = build_opportunity_summary(cluster.id, db)

    # The demand floor count must be > 0 for approved demand-role sources.
    demand_role_count = summary["demand_metrics"]["demand_role_item_count"]
    assert demand_role_count > 0, (
        f"KEEP_DEMAND items from '{source_name}' must count toward demand floor; "
        f"got demand_role_item_count={demand_role_count}"
    )
    assert summary["eligible_evidence_count"] >= 3


# ---------------------------------------------------------------------------
# Req 3.7 — upgrade_existing_offering angle requires cites_known_offering
# Validates: Requirements 3.7
# ---------------------------------------------------------------------------


def test_upgrade_angle_requires_cites_known_offering():
    """Req 3.7: upgrade_existing_offering always requires a known offering citation.

    If text does NOT cite any KNOWN_OFFERING_MARKERS,
    is_product_ready must return product_ready=False with a relevant blocker.

    **Validates: Requirements 3.7**
    """
    # Confirm the text does not cite any known offering.
    text_without_offering = "a generic analytics pipeline monitoring dashboard"
    assert not cites_known_offering(text_without_offering), (
        "Pre-condition: test text must not cite any KNOWN_OFFERING_MARKERS"
    )

    product_ready, blockers = is_product_ready(
        top10_eligible=True,
        offering_angle="upgrade_existing_offering",
        components={
            "leverage_on_offerings": 4,
            "demand_strength": 4,
            "capability_readiness": 4,
            "section_client_fit": 4,
            "commercial_signal": 3,
            "effort_to_first_version": 3,
            "defensibility": 3,
        },
        smallest_first_version=text_without_offering,
        first_client_type="data analyst",
        failure_risk_line="Vendor native tool may ship",
        keep_demand_count=3,
        title="Pipeline monitoring",
        problem_texts=["pipeline monitoring solution for cloud data teams"],
    )

    assert product_ready is False, (
        "upgrade_existing_offering without cites_known_offering must return "
        f"product_ready=False; blockers={blockers}"
    )
    # There must be a blocker mentioning the known offering requirement.
    blocker_text = " ".join(blockers).casefold()
    assert "known" in blocker_text or "offering" in blocker_text or "cite" in blocker_text, (
        f"Expected a known-offering blocker; got: {blockers}"
    )


# ---------------------------------------------------------------------------
# Req 3.4 — Single-source cap at ≤ 60
# References the existing test_single_source_platform_caps_at_60 in test_tatvic_scoring.py.
# Validates: Requirements 3.4
# ---------------------------------------------------------------------------


def test_single_source_cap_still_holds():
    """Req 3.4: A cluster with evidence from one platform must be capped at ≤ 60.

    This mirrors the pattern in tests/test_tatvic_scoring.py::test_single_source_platform_caps_at_60.

    **Validates: Requirements 3.4**
    """
    analyses = [
        {
            "section": "Analytics",
            "filter_reason_code": "KEEP_DEMAND",
            "problem_statement": "GA4 attribution lag for paid media",
            "capability_readiness_1_to_5": 4,
            "section_client_fit_1_to_5": 4,
            "leverage_on_offerings_1_to_5": 4,
            "commercial_signal_1_to_5": 3,
            "effort_to_first_version_1_to_5": 4,
            "defensibility_1_to_5": 3,
            "smallest_first_version": "GA4 Tag Auditor check for attribution windows",
            "first_client_type": "performance marketer",
            "failure_risk_line": "Native GA4 reports may suffice",
        }
    ] * 5

    class Sub:
        def __init__(self, sid: int):
            self.id = sid
            self.submitted_by = f"author{sid}"
            self.source_name = "reddit"
            self.created_at = None

    result = score_tatvic_opportunity(
        eligible_analyses=analyses,
        submissions=[Sub(i) for i in range(5)],
        source_platforms={"reddit"},
        item_count=5,
        distinct_authors=5,
        records_last_30d=5,
        records_prior_30d=1,
        primary_section="Analytics",
        cluster_title="GA4 attribution lag",
    )

    assert result.single_source_capped is True, (
        "single_source_capped must be True for a single-platform cluster"
    )
    if result.score is not None:
        assert result.score <= 60, (
            f"Single-source cluster score {result.score} exceeds 60"
        )


# ---------------------------------------------------------------------------
# Req 3.7 indirect — Complete-fields path in _offering_angle unchanged
# Validates: Requirements 3.7 (indirect)
# ---------------------------------------------------------------------------


def test_complete_fields_offering_angle_uses_existing_logic():
    """Req 3.7 indirect: When all three LLM fields are present, _offering_angle
    must follow the existing angle-selection logic (not a new fallback).

    **Validates: Requirements 3.7**
    """
    components = {
        "leverage_on_offerings": 4,
        "demand_strength": 4,
        "capability_readiness": 4,
    }
    analyses = [
        {
            "smallest_first_version": "BQ Agent check for GA4 vs order mismatch",
            "first_client_type": "eCommerce analytics lead",
            "failure_risk_line": "Native GA4 tools may shrink demand",
            "leverage_on_offerings_1_to_5": 4,
            "filter_reason_code": "KEEP_DEMAND",
        }
    ]
    angle, smallest, client, failure = _offering_angle(components, analyses, in_scope=True)

    # With all fields present and leverage >= 4 + cites known offering → upgrade
    assert angle == "upgrade_existing_offering", (
        f"Complete-fields path expected 'upgrade_existing_offering'; got '{angle}'"
    )
    assert smallest is not None
    assert client is not None
    assert failure is not None


def test_complete_fields_new_offering_angle():
    """Complete-fields path: leverage 3 + demand 3 + readiness 3 → new_agent_or_offering.

    **Validates: Requirements 3.7**
    """
    components = {
        "leverage_on_offerings": 3,
        "demand_strength": 3,
        "capability_readiness": 3,
    }
    analyses = [
        {
            "smallest_first_version": "BQ Agent check for GA4 vs order mismatch",
            "first_client_type": "eCommerce analytics lead",
            "failure_risk_line": "Native GA4 tools may shrink demand",
            "leverage_on_offerings_1_to_5": 3,
            "filter_reason_code": "KEEP_DEMAND",
        }
    ]
    angle, smallest, client, failure = _offering_angle(components, analyses, in_scope=True)

    # leverage=3 + demand=3 + readiness=3 with GA4 in text → new_agent_or_offering
    assert angle == "new_agent_or_offering", (
        f"Complete-fields new-offering path expected 'new_agent_or_offering'; got '{angle}'"
    )


# ---------------------------------------------------------------------------
# Req 3.3 — DROP codes excluded
# A cluster with only DROP-coded items → top10_eligible=False
# Validates: Requirements 3.3
# ---------------------------------------------------------------------------


def test_drop_coded_items_excluded_from_demand_floor(db, cluster):
    """Req 3.3: DROP-coded submissions must not count toward the demand floor.

    A cluster with only DROP-coded items must have demand_role_item_count=0
    and top10_eligible=False.

    **Validates: Requirements 3.3**
    """
    for idx, source in enumerate(
        ("Reddit", "Stack Exchange:stackoverflow", "Stack Exchange:datascience")
    ):
        _add_submission(
            db,
            cluster.id,
            source=source,
            author=f"author_{idx}",
            url_suffix=f"drop_{idx}",
            code="DROP_OUT_OF_SCOPE_TOPIC",
        )

    summary = build_opportunity_summary(cluster.id, db)

    assert summary["eligible_evidence_count"] == 0, (
        "DROP-coded items must not appear in eligible_evidence_count"
    )
    assert summary["top10_eligible"] is False, (
        "Cluster with only DROP-coded items must have top10_eligible=False"
    )


# ---------------------------------------------------------------------------
# Req 3.2 — Fewer than 3 items → watchlist only
# Validates: Requirements 3.2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_items", [1, 2])
def test_fewer_than_3_items_not_top10_eligible(db, cluster, n_items):
    """Req 3.2: A cluster with fewer than 3 items must remain watchlist only.

    top10_eligible must be False when keep_demand_count < 3.

    **Validates: Requirements 3.2**
    """
    sources = ["Reddit", "Stack Exchange:stackoverflow"]
    for idx in range(n_items):
        _add_submission(
            db,
            cluster.id,
            source=sources[idx % len(sources)],
            author=f"author_{idx}",
            url_suffix=f"few_{idx}",
        )

    summary = build_opportunity_summary(cluster.id, db)

    assert summary["top10_eligible"] is False, (
        f"Cluster with {n_items} item(s) must not be top10_eligible"
    )
    assert summary["product_ready"] is False, (
        f"Cluster with {n_items} item(s) must not be product_ready"
    )
