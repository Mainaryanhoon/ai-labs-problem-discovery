"""
Bug Condition Exploration Tests — pipeline-opportunity-surfacing-bug

These tests encode the EXPECTED (correct) behaviour for three known defects.
They are EXPECTED TO FAIL on unfixed code — failure confirms the bugs exist.
DO NOT attempt to fix the code when these tests fail.

Bug Condition A: Defect 1 — source-role mismatch silently zeroes demand floor diagnostic
Bug Condition B: Defect 2 — _offering_angle returns "undetermined" when any LLM field is absent
Bug Condition C: Defect 3 — cites_known_offering gate is too strict for in-scope sections

Validates: Requirements 1.1, 1.2, 1.3, 1.4
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import build_opportunity_summary
from app.models import (
    ClusterEvidence,
    ProblemCluster,
    Submission,
)
from app.tatvic_scoring import _offering_angle, is_product_ready


# ---------------------------------------------------------------------------
# Shared in-memory DB fixture (mirrors tests/test_opportunity_scoring.py)
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


def _make_cluster(db, title="Pipeline monitoring cluster"):
    cluster = ProblemCluster(
        title=title,
        problem_statement="Cloud data teams struggle with pipeline monitoring.",
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


def _add_hn_keep_demand(db, cluster_id, index: int):
    """Add a KEEP_DEMAND submission from hacker_news (non-demand-role source)."""
    analysis = {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "problem_statement": f"Pipeline monitoring is painful for cloud teams #{index}.",
        "urgency_1_to_5": 4,
        "frequency_signal_1_to_5": 4,
        "evidence_strength_1_to_5": 4,
        "willingness_to_pay_signal": "none",
        "software_buildability": "likely",
        "evidence_quotes": [f"pipeline monitoring is painful #{index}"],
        "section": "Analytics",
        "filter_reason_code": "KEEP_DEMAND",
        "capability_readiness_1_to_5": 3,
        "section_client_fit_1_to_5": 3,
        "leverage_on_offerings_1_to_5": 3,
        "commercial_signal_1_to_5": 2,
        "effort_to_first_version_1_to_5": 3,
        "defensibility_1_to_5": 2,
        "smallest_first_version": "Pipeline monitoring dashboard for GA4",
        "first_client_type": "data engineer",
        "failure_risk_line": "Vendor native tool may ship",
        "affected_user": "data engineer",
    }
    text = f"pipeline monitoring is painful #{index}"
    submission = Submission(
        text=text,
        source_name="hacker_news",
        source_url=f"https://news.ycombinator.com/item?id=100{index}",
        submitted_by=f"hn_user_{index}",
        language="en",
        analysis_status="completed",
        analysis_json=json.dumps(analysis),
    )
    db.add(submission)
    db.flush()

    evidence = ClusterEvidence(
        cluster_id=cluster_id,
        submission_id=submission.id,
        is_independent=True,
        relationship_type="supports",
    )
    db.add(evidence)
    db.commit()
    return submission


# ---------------------------------------------------------------------------
# Bug Condition A — Defect 1: source-role mismatch silently zeroes demand floor
#
# Validates: Requirements 1.1, 1.2
#
# Expected behaviour (post-fix): demand_metrics["role_blocked_keep_demand_count"] >= 1
# Current behaviour (bug):       count is 0 or key is missing from demand_metrics
# ---------------------------------------------------------------------------


def test_bug_condition_a_role_blocked_keep_demand_count_exposed(db):
    """
    **Validates: Requirements 1.1, 1.2**

    A cluster with 3 KEEP_DEMAND submissions from hacker_news (a non-demand-role
    source) MUST expose role_blocked_keep_demand_count >= 1 in demand_metrics.

    This test MUST FAIL on unfixed code — failure confirms Defect 1 exists.
    """
    cluster = _make_cluster(db)

    # Attach 3 KEEP_DEMAND submissions from hacker_news (non-demand-role source)
    for i in range(3):
        _add_hn_keep_demand(db, cluster.id, index=i)

    result = build_opportunity_summary(cluster.id, db)

    # The demand_metrics dict MUST exist and MUST surface the blocked count
    assert "demand_metrics" in result, (
        "BUG A: demand_metrics key is missing from opportunity summary"
    )
    demand_metrics = result["demand_metrics"]
    assert "role_blocked_keep_demand_count" in demand_metrics, (
        "BUG A: role_blocked_keep_demand_count key is missing from demand_metrics — "
        "the system is silently dropping KEEP_DEMAND items from non-demand-role sources "
        "without surfacing any diagnostic count."
    )
    count = demand_metrics["role_blocked_keep_demand_count"]
    assert count >= 1, (
        f"BUG A: role_blocked_keep_demand_count is {count}, expected >= 1. "
        f"All 3 KEEP_DEMAND submissions from hacker_news should be reported as "
        f"blocked by source-role policy, but the diagnostic count is zero."
    )


# ---------------------------------------------------------------------------
# Bug Condition B — Defect 2: _offering_angle returns "undetermined" when any
# LLM field is absent (failure_risk_line is None here)
#
# Validates: Requirements 1.3, 2.3
#
# Expected behaviour (post-fix): angle is NOT "undetermined" when in_scope=True
#                                 and scores are sufficient
# Current behaviour (bug):       returns "undetermined" immediately
# ---------------------------------------------------------------------------


def test_bug_condition_b_offering_angle_not_undetermined_with_missing_field(
):
    """
    **Validates: Requirements 1.3, 2.3**

    When an in-scope cluster has leverage >= 3, demand >= 3, readiness >= 3
    but failure_risk_line is None, _offering_angle MUST NOT return "undetermined".
    It must return a score-based fallback angle instead.

    This test MUST FAIL on unfixed code — failure confirms Defect 2 exists.
    """
    components = {
        "leverage_on_offerings": 3,
        "demand_strength": 3,
        "capability_readiness": 3,
    }
    analyses = [
        {
            "smallest_first_version": "A tool",
            "first_client_type": "analyst",
            "failure_risk_line": None,    # <-- missing field that triggers the bug
            "leverage_on_offerings_1_to_5": 3,
        }
    ]

    angle, *_ = _offering_angle(components, analyses, in_scope=True)

    assert angle != "undetermined", (
        f"BUG B: _offering_angle returned 'undetermined' even though in_scope=True "
        f"and scores are sufficient (leverage=3, demand=3, readiness=3). "
        f"The function is blocking product-readiness by refusing to produce a "
        f"score-based fallback when failure_risk_line is None."
    )


# ---------------------------------------------------------------------------
# Bug Condition C — Defect 3: cites_known_offering gate is too strict for
# in-scope sections (blocks product_ready even when section is Analytics etc.)
#
# Validates: Requirements 1.4, 2.4
#
# Expected behaviour (post-fix): product_ready is True when in-scope and
#                                 offering_angle != "upgrade_existing_offering"
# Current behaviour (bug):       product_ready is False due to cites_known_offering blocker
# ---------------------------------------------------------------------------


def test_bug_condition_c_product_ready_not_blocked_by_known_offering_for_in_scope(
):
    """
    **Validates: Requirements 1.4, 2.4**

    An in-scope cluster (Analytics section) with offering_angle="new_agent_or_offering",
    keep_demand_count >= 1, top10_eligible=True, and all required text fields present
    MUST NOT be blocked by "Does not cite a known Tatvic offering" when the section
    itself is in-scope (Analytics, Cloud and Data Engineering, etc.).

    The problem statement "pipeline monitoring solution for cloud data teams" does not
    contain any KNOWN_OFFERING_MARKERS, which currently triggers the blocker even for
    genuinely Tatvic-relevant problems.

    This test MUST FAIL on unfixed code — failure confirms Defect 3 exists.
    """
    product_ready, blockers = is_product_ready(
        top10_eligible=True,
        offering_angle="new_agent_or_offering",
        components={
            "leverage_on_offerings": 3,
            "demand_strength": 3,
            "capability_readiness": 3,
        },
        smallest_first_version="A pipeline monitoring dashboard",
        first_client_type="data engineer",
        failure_risk_line="Vendor native tool may ship",
        keep_demand_count=3,
        title="Pipeline monitoring",
        problem_texts=["pipeline monitoring solution for cloud data teams"],
        in_scope_section=True,
    )

    assert product_ready is True, (
        f"BUG C: is_product_ready returned False with blockers: {blockers}. "
        f"An in-scope Analytics cluster with offering_angle='new_agent_or_offering' "
        f"and all required fields present MUST NOT be blocked by the "
        f"cites_known_offering keyword gate when the section is in-scope. "
        f"The current code unconditionally applies the keyword gate regardless of "
        f"section membership."
    )
