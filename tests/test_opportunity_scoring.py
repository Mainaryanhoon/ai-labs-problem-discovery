
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import get_cluster, get_cluster_opportunity
from app.models import (
    ClusterEvidence,
    ProblemCluster,
    SourceRecord,
    Submission,
)


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
        "software_buildability": "likely",
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
    assert result["eligible_distinct_source_record_count"] == 1
    assert result["unique_source_platform_count"] == 1


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


def test_duplicate_copy_does_not_change_score_or_corroboration(db, cluster):
    text = "I struggle to reconcile orders across delivery apps."
    add_evidence(
        db,
        cluster.id,
        text=text,
        url="https://example.com/original",
        analysis=valid_analysis() | {
            "evidence_quotes": ["struggle to reconcile orders"],
        },
    )
    baseline = get_result(db, cluster)

    add_evidence(
        db,
        cluster.id,
        text=f"  {text.upper()} ",
        url="https://example.com/copy",
        source="copied-platform",
        analysis=valid_analysis() | {
            "willingness_to_pay_signal": "explicit",
            "evidence_quotes": ["struggle to reconcile orders"],
        },
        independent=True,
    )
    after_copy = get_result(db, cluster)

    assert after_copy["score"] == baseline["score"]
    assert after_copy["evidence_level"] == baseline["evidence_level"]
    assert after_copy["distinct_source_records"] == (
        baseline["distinct_source_records"]
    )
    assert after_copy["unique_source_platform_count"] == (
        baseline["unique_source_platform_count"]
    )
    assert after_copy["recorded_independent_records"] == (
        baseline["recorded_independent_records"]
    )
    assert after_copy["commercial_signal"] == baseline["commercial_signal"]
    assert after_copy["duplicate_analyzed_records_excluded"] == 1


def test_repeated_requests_return_stable_score_and_metrics(db, cluster):
    add_evidence(
        db,
        cluster.id,
        text="Owners spend hours reconciling orders manually.",
        url=None,
        analysis=valid_analysis() | {
            "evidence_quotes": ["spend hours reconciling orders"],
        },
    )

    first = get_result(db, cluster)
    second = get_result(db, cluster)

    assert second == first


def test_growth_counts_only_recent_source_timestamps(db, cluster):
    now = datetime.now(timezone.utc)
    source_dated = add_evidence(
        db,
        cluster.id,
        text="Owners reconcile delivery orders every week.",
        url="https://example.com/source-dated",
        analysis=valid_analysis(),
    )
    source_dated.created_at = now - timedelta(days=2)
    db.add(SourceRecord(
        submission_id=source_dated.id,
        source_name=source_dated.source_name,
        native_id="101",
        timestamp_origin="source",
    ))

    collection_dated = add_evidence(
        db,
        cluster.id,
        text="Shop owners match stock and invoices daily.",
        url="https://example.com/collection-dated",
        analysis=valid_analysis(),
    )
    collection_dated.created_at = now
    db.add(SourceRecord(
        submission_id=collection_dated.id,
        source_name=collection_dated.source_name,
        native_id="102",
        timestamp_origin="collection_time",
    ))

    stale_source_dated = add_evidence(
        db,
        cluster.id,
        text="Small shops update orders in a manual spreadsheet.",
        url="https://example.com/stale-source-dated",
        analysis=valid_analysis(),
    )
    stale_source_dated.created_at = now - timedelta(days=10)
    db.add(SourceRecord(
        submission_id=stale_source_dated.id,
        source_name=stale_source_dated.source_name,
        native_id="103",
        timestamp_origin="source",
    ))
    db.commit()

    result = get_result(db, cluster)

    assert result["source_timestamped_records_7d"] == 1
    assert any(
        "adds 5 growth points" in reason
        for reason in result["reasons"]
    )


def test_empty_cluster_has_insufficient_evidence_state(db, cluster):
    result = get_result(db, cluster)

    assert result["score"] is None
    assert result["evidence_level"] == "insufficient_analyzed_evidence"
    assert result["raw_attached_record_count"] == 0
    assert result["analyzed_submission_count"] == 0
    assert result["unique_evidence_count"] == 0


def test_noise_does_not_contribute_scores_or_commercial_signal(db, cluster):
    actionable = valid_analysis() | {
        "urgency_1_to_5": 2,
        "frequency_signal_1_to_5": 2,
        "evidence_strength_1_to_5": 2,
        "willingness_to_pay_signal": "none",
        "evidence_quotes": [],
    }
    add_evidence(
        db,
        cluster.id,
        text="Owners manually reconcile orders every week.",
        url="https://example.com/actionable",
        analysis=actionable,
    )
    baseline = get_result(db, cluster)

    rejected = valid_analysis() | {
        "is_genuine_problem": False,
        "signal_type": "noise",
        "urgency_1_to_5": 5,
        "frequency_signal_1_to_5": 5,
        "evidence_strength_1_to_5": 5,
        "willingness_to_pay_signal": "explicit",
        "evidence_quotes": ["A fabricated buying claim"],
    }
    add_evidence(
        db,
        cluster.id,
        text="Unrelated promotional content",
        url="https://example.com/noise",
        source="another-platform",
        analysis=rejected,
    )
    result = get_result(db, cluster)

    assert result["score"] == baseline["score"]
    assert result["distinct_source_records"] == 2
    assert result["eligible_distinct_source_record_count"] == 1
    assert result["unique_source_platform_count"] == 2
    assert result["eligible_evidence_count"] == 1
    assert result["unique_evidence_count"] == 2
    assert result["commercial_signal"] == "none"


def test_untraceable_quotes_are_not_used_in_scoring(db, cluster):
    add_evidence(
        db,
        cluster.id,
        text="Owners spend hours reconciling orders manually.",
        url=None,
        analysis=valid_analysis() | {
            "evidence_quotes": ["This quote does not occur in the source."],
        },
    )

    result = get_result(db, cluster)

    assert result["score"] is not None
    assert any("No direct evidence excerpts" in item
               for item in result["limitations"])


def test_untraceable_quote_cannot_support_commercial_signal(db, cluster):
    add_evidence(
        db,
        cluster.id,
        text="Owners spend hours reconciling orders manually.",
        url="https://example.com/purchase-claim",
        analysis=valid_analysis() | {
            "willingness_to_pay_signal": "explicit",
            "evidence_quotes": ["I will pay for this product."],
        },
    )

    result = get_result(db, cluster)

    assert result["commercial_signal"] == "unclear"
    assert result["score"] < 70
    assert any(
        "lacked a traceable quote" in item
        for item in result["limitations"]
    )


def test_boolean_and_out_of_range_ratings_cannot_inflate_score(
    db, cluster
):
    add_evidence(
        db,
        cluster.id,
        text="Owners struggle to reconcile delivery orders.",
        url=None,
        analysis=valid_analysis() | {
            "urgency_1_to_5": True,
            "frequency_signal_1_to_5": 99,
            "evidence_strength_1_to_5": False,
        },
    )

    result = get_result(db, cluster)

    assert result["score"] is None
    assert result["evidence_level"] == "insufficient_evidence"


def test_mixed_purchase_signals_explain_any_record_aggregation(db, cluster):
    text = "Owners spend hours reconciling orders manually."
    add_evidence(
        db,
        cluster.id,
        text=text,
        url="https://example.com/explicit",
        analysis=valid_analysis() | {
            "willingness_to_pay_signal": "explicit",
            "evidence_quotes": ["spend hours reconciling orders"],
        },
    )
    add_evidence(
        db,
        cluster.id,
        text="Retailers manually compare delivery statements weekly.",
        url="https://example.com/no-signal",
        analysis=valid_analysis() | {
            "willingness_to_pay_signal": "none",
        },
    )

    result = get_result(db, cluster)

    assert result["commercial_signal"] == "explicit"
    assert "at least one eligible record" in (
        result["commercial_signal_limitation"]
    )
    assert result["verified_independent_records"] == 0
    assert result["verified_independent_source_count"] == 0


def test_empty_source_text_without_url_cannot_create_corroboration(db, cluster):
    for index in range(2):
        add_evidence(
            db,
            cluster.id,
            text="",
            url=None,
            source=f"platform-{index}",
            analysis=valid_analysis() | {
                "evidence_quotes": ["untraceable"],
            },
            independent=True,
        )

    result = get_result(db, cluster)

    assert result["unique_evidence_count"] == 2
    assert result["eligible_evidence_count"] == 2
    assert result["distinct_source_records"] == 0
    assert result["eligible_distinct_source_record_count"] == 0
    assert result["unique_source_platform_count"] == 2
    assert result["recorded_independent_records"] == 0
    assert result["verified_independent_records"] == 0
    assert result["evidence_level"] == "source_identity_unverified"
    assert any(
        "No usable source identifier" in limitation
        for limitation in result["limitations"]
    )


def test_weak_and_contradictory_records_cannot_become_strong(
    db, cluster
):
    add_evidence(
        db,
        cluster.id,
        text="Owners manually reconcile orders every day.",
        url="https://example.com/strong",
        analysis=valid_analysis() | {
            "urgency_1_to_5": 5,
            "frequency_signal_1_to_5": 5,
            "evidence_strength_1_to_5": 5,
            "evidence_quotes": ["manually reconcile orders"],
        },
    )
    add_evidence(
        db,
        cluster.id,
        text="One person occasionally dislikes a minor workflow.",
        url="https://example.com/weak",
        analysis=valid_analysis() | {
            "urgency_1_to_5": 1,
            "frequency_signal_1_to_5": 1,
            "evidence_strength_1_to_5": 1,
            "evidence_quotes": [],
        },
    )

    result = get_result(db, cluster)

    assert result["unique_evidence_count"] == 2
    assert result["eligible_evidence_count"] == 2
    assert result["score"] < 35
    assert result["evidence_level"] == "multi_source_unverified"


def test_duplicate_recorded_independence_does_not_increase_counts(
    db, cluster
):
    text = "Owners struggle to reconcile delivery orders manually."
    add_evidence(
        db,
        cluster.id,
        text=text,
        url="https://example.com/post/original",
        analysis=valid_analysis(),
        independent=True,
    )
    add_evidence(
        db,
        cluster.id,
        text=f"  {text.upper()} ",
        url="https://example.com/post/copy",
        source="other-platform",
        analysis=valid_analysis(),
        independent=True,
    )

    opportunity = get_result(db, cluster)
    details = get_cluster(cluster.id, db)

    assert opportunity["unique_evidence_count"] == 1
    assert opportunity["recorded_independent_records"] == 1
    assert details["evidence_count"] == 2
    assert details["independent_evidence_count"] == 1


def test_contradicting_and_mention_only_links_do_not_corroborate(db, cluster):
    add_evidence(
        db,
        cluster.id,
        text="Owners manually reconcile orders every week.",
        url="https://example.com/supports",
        analysis=valid_analysis() | {
            "willingness_to_pay_signal": "none",
            "evidence_quotes": [],
        },
    )
    baseline = get_result(db, cluster)

    for relationship, slug in (
        ("contradicts", "contradicts"),
        ("mentions", "mentions"),
    ):
        submission = add_evidence(
            db,
            cluster.id,
            text=f"Strongly worded {slug} claim with an explicit purchase.",
            url=f"https://example.com/{slug}",
            source=f"platform-{slug}",
            analysis=valid_analysis() | {
                "urgency_1_to_5": 5,
                "frequency_signal_1_to_5": 5,
                "evidence_strength_1_to_5": 5,
                "willingness_to_pay_signal": "explicit",
                "evidence_quotes": ["Strongly worded"],
            },
        )
        link = db.scalar(
            select(ClusterEvidence).where(
                ClusterEvidence.submission_id == submission.id
            )
        )
        link.relationship_type = relationship
        db.commit()

    result = get_result(db, cluster)

    assert result["score"] == baseline["score"]
    assert result["eligible_evidence_count"] == 1
    assert result["eligible_distinct_source_record_count"] == 1
    assert result["commercial_signal"] == "none"
    assert result["raw_attached_record_count"] == 3
    scoring_status = {
        item["relationship_type"]: item["scoring_status"]
        for item in result["evidence_provenance"]
    }
    assert scoring_status["supports"] == "included"
    assert scoring_status["contradicts"] == "not_scored"
    assert scoring_status["mentions"] == "not_scored"


def test_legacy_initial_evidence_remains_supporting(db, cluster):
    submission = add_evidence(
        db,
        cluster.id,
        text="Shop owners struggle to reconcile delivery orders.",
        url="https://example.com/initial-evidence",
        analysis=valid_analysis(),
    )
    link = db.scalar(
        select(ClusterEvidence).where(
            ClusterEvidence.submission_id == submission.id
        )
    )
    link.relationship_type = "initial_evidence"
    db.commit()

    result = get_result(db, cluster)

    assert result["eligible_evidence_count"] == 1
    provenance = next(
        item
        for item in result["evidence_provenance"]
        if item["submission_id"] == submission.id
    )
    assert provenance["scoring_status"] == "included"
