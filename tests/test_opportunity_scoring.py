
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import get_cluster_opportunity
from app.models import ClusterEvidence, ProblemCluster, Submission


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
        title="Delivery order reconciliation",
        problem_statement=(
            "Retailers struggle to reconcile orders across platforms."
        ),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def add_evidence(
    db,
    cluster_id,
    *,
    text,
    url,
    source="reddit",
    analysis=None,
    status="completed",
    independent=False,
):
    submission = Submission(
        text=text,
        source_name=source,
        source_url=url,
        language="en",
        analysis_status=status,
        analysis_json=(
            json.dumps(analysis)
            if analysis is not None
            else None
        ),
    )
    db.add(submission)
    db.flush()

    evidence = ClusterEvidence(
        cluster_id=cluster_id,
        submission_id=submission.id,
        is_independent=independent,
        relationship_type="supports",
    )
    db.add(evidence)
    db.commit()
    return submission


def valid_analysis():
    return {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "urgency_1_to_5": 4,
        "frequency_signal_1_to_5": 4,
        "evidence_strength_1_to_5": 4,
        "willingness_to_pay_signal": "none",
        "evidence_quotes": ["This workflow is frustrating."],
    }


def get_result(db, cluster):
    return get_cluster_opportunity(cluster.id, db)


def test_tracking_parameter_url_duplicates_count_once(db, cluster):
    add_evidence(
        db,
        cluster.id,
        text="Reconciling orders across delivery platforms is difficult.",
        url="https://example.com/post/1?utm_source=reddit",
        analysis=valid_analysis(),
    )
    add_evidence(
        db,
        cluster.id,
        text="A different version of the same post.",
        url="https://example.com/post/1?utm_medium=social",
        analysis=valid_analysis(),
    )

    result = get_result(db, cluster)

    assert result["raw_attached_record_count"] == 2
    assert result["analyzed_submission_count"] == 2
    assert result["unique_evidence_count"] == 1
    assert result["duplicate_analyzed_records_excluded"] == 1
    assert result["distinct_source_records"] == 1


def test_normalized_text_duplicates_count_once(db, cluster):
    shared_text = "Retailers struggle to reconcile delivery orders."

    add_evidence(
        db,
        cluster.id,
        text=shared_text,
        url="https://example.com/post/1",
        analysis=valid_analysis(),
    )
    add_evidence(
        db,
        cluster.id,
        text=f"  {shared_text.upper()}  ",
        url="https://example.com/post/2",
        analysis=valid_analysis(),
        source="hacker_news",
    )

    result = get_result(db, cluster)

    assert result["raw_attached_record_count"] == 2
    assert result["unique_evidence_count"] == 1
    assert result["duplicate_analyzed_records_excluded"] == 1
    assert result["unique_source_platform_count"] == 1


def test_distinct_evidence_from_same_platform_counts_separately(db, cluster):
    add_evidence(
        db,
        cluster.id,
        text="Matching orders across apps takes too long.",
        url="https://example.com/post/1",
        source="reddit",
        analysis=valid_analysis(),
    )
    add_evidence(
        db,
        cluster.id,
        text="I manually reconcile three delivery dashboards daily.",
        url="https://example.com/post/2",
        source="reddit",
        analysis=valid_analysis(),
    )

    result = get_result(db, cluster)

    assert result["raw_attached_record_count"] == 2
    assert result["unique_evidence_count"] == 2
    assert result["distinct_source_records"] == 2
    assert result["unique_source_platform_count"] == 1
    assert result["evidence_level"] == "multi_source_unverified"


def test_recorded_independence_does_not_mean_verified_independence(
    db, cluster
):
    for index in range(2):
        add_evidence(
            db,
            cluster.id,
            text=f"Distinct reconciliation complaint number {index}.",
            url=f"https://example.com/post/{index}",
            analysis=valid_analysis(),
            independent=True,
        )

    result = get_result(db, cluster)

    assert result["recorded_independent_records"] == 2
    assert result["evidence_level"] == "multi_source_unverified"
    assert any(
        "not been verified" in limitation
        or "have not been verified" in limitation
        for limitation in result["limitations"]
    )


def test_malformed_and_pending_analyses_are_excluded(db, cluster):
    pending = add_evidence(
        db,
        cluster.id,
        text="A pending submission.",
        url="https://example.com/pending",
        status="pending",
    )
    malformed = add_evidence(
        db,
        cluster.id,
        text="A malformed analysis.",
        url="https://example.com/malformed",
        analysis=None,
    )
    malformed.analysis_status = "completed"
    malformed.analysis_json = "{not valid json"
    db.commit()

    result = get_result(db, cluster)

    assert result["score"] is None
    assert result["evidence_level"] == "insufficient_analyzed_evidence"
    assert result["raw_attached_record_count"] == 2
    assert result["unique_evidence_count"] == 0
    assert result["analyzed_submission_count"] == 0


def test_missing_urls_do_not_crash_and_distinct_texts_survive(db, cluster):
    add_evidence(
        db,
        cluster.id,
        text="Reconciling inventory is slow.",
        url=None,
        analysis=valid_analysis(),
    )
    add_evidence(
        db,
        cluster.id,
        text="Matching payouts manually is slow.",
        url=None,
        analysis=valid_analysis(),
    )

    result = get_result(db, cluster)

    assert result["unique_evidence_count"] == 2
    assert result["distinct_source_records"] == 2


def test_duplicate_records_are_not_given_extra_scoring_weight(db, cluster):
    add_evidence(
        db,
        cluster.id,
        text="I struggle to reconcile orders across delivery apps.",
        url="https://example.com/original",
        analysis=valid_analysis(),
    )
    add_evidence(
        db,
        cluster.id,
        text="I struggle to reconcile orders across delivery apps.",
        url="https://example.com/copied",
        analysis=valid_analysis(),
    )

    result = get_result(db, cluster)

    assert result["raw_attached_record_count"] == 2
    assert result["unique_evidence_count"] == 1
    assert result["score"] is not None
    assert result["evidence_level"] == "single_source_candidate"
