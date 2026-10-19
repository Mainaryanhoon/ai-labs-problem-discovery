
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import ClusterEvidence, ProblemCluster, Submission
from app.clustering import (
    AUTO_MATCH_THRESHOLD,
    cosine_similarity,
    normalize_text,
    cluster_submission,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    Base.metadata.create_all(bind=engine)

    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def make_analyzed_submission(db, text, problem_statement=None):
    analysis = {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "problem_statement": problem_statement or text,
        "affected_user": "Independent shop owners",
        "category": "Business Operations",
        "industry": "Retail",
        "urgency_1_to_5": 3,
        "frequency_signal_1_to_5": 3,
        "evidence_strength_1_to_5": 3,
        "evidence_quotes": [text],
    }
    submission = Submission(
        text=text,
        source_name="test",
        analysis_status="completed",
        analysis_json=json.dumps(analysis),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def test_normalize_text_ignores_case_and_punctuation():
    assert normalize_text(
        "Shop owners struggle with delivery apps!"
    ) == normalize_text(
        "SHOP owners struggle with delivery apps."
    )


def test_cosine_similarity_identical_vectors():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_auto_match_threshold_is_valid():
    assert 0.0 < AUTO_MATCH_THRESHOLD <= 1.0


def test_unanalyzed_submission_is_rejected(db):
    submission = Submission(
        text="Shop owners struggle to reconcile delivery orders.",
        source_name="test",
        analysis_status="pending",
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    with pytest.raises(ValueError):
        cluster_submission(db, submission)


def test_existing_evidence_prevents_reclustering(db):
    submission = make_analyzed_submission(
        db,
        "Shop owners struggle to reconcile delivery orders.",
    )
    cluster = ProblemCluster(
        title="Delivery reconciliation",
        problem_statement="Shop owners struggle to reconcile delivery orders.",
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    db.add(
        ClusterEvidence(
            cluster_id=cluster.id,
            submission_id=submission.id,
            is_independent=False,
            relationship_type="initial_evidence",
        )
    )
    db.commit()

    result = cluster_submission(db, submission)

    assert result["decision"] == "already_clustered"
    assert db.scalar(
        select(ClusterEvidence).where(
            ClusterEvidence.submission_id == submission.id
        )
    ) is not None
    assert len(db.scalars(select(ProblemCluster)).all()) == 1



def test_embedding_failure_does_not_create_cluster(db, monkeypatch):
    # An existing cluster forces the semantic matching path.
    db.add(
        ProblemCluster(
            title="Existing delivery problem",
            problem_statement="Retailers struggle to reconcile delivery orders.",
            affected_user="Independent shop owners",
            category="Business Operations",
            industry="Retail",
        )
    )
    db.commit()

    submission = make_analyzed_submission(
        db,
        "Shop owners struggle to reconcile delivery orders.",
    )

    def fail_embeddings(_texts):
        raise RuntimeError("Simulated embedding failure")

    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        fail_embeddings,
    )

    with pytest.raises(RuntimeError, match="Simulated embedding failure"):
        cluster_submission(db, submission)

    # The pre-existing cluster remains; no new cluster or evidence is created.
    assert len(db.scalars(select(ProblemCluster)).all()) == 1
    assert db.scalars(select(ClusterEvidence)).all() == []




def test_strong_semantic_match_attaches_evidence(db, monkeypatch):
    cluster = ProblemCluster(
        title="Delivery reconciliation",
        problem_statement="Retailers struggle to reconcile delivery orders.",
        affected_user="Independent shop owners",
        category="Business Operations",
        industry="Retail",
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    submission = make_analyzed_submission(
        db,
        "Shop owners struggle to match delivery app orders.",
        problem_statement="Retailers struggle to reconcile delivery orders.",
    )

    # First vector is the submission; second is the existing cluster.
    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        lambda _texts: [[1.0, 0.0], [1.0, 0.0]],
    )

    result = cluster_submission(db, submission)

    assert result["decision"] == "matched"
    assert result["cluster_id"] == cluster.id
    assert result["similarity"] == pytest.approx(1.0)
    assert result["independence_verified"] is False

    evidence = db.scalars(select(ClusterEvidence)).all()
    assert len(evidence) == 1
    assert evidence[0].submission_id == submission.id
    assert evidence[0].is_independent is False


def test_borderline_semantic_match_requires_review(db, monkeypatch):
    cluster = ProblemCluster(
        title="Existing problem",
        problem_statement="Retailers struggle with delivery reconciliation.",
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    submission = make_analyzed_submission(
        db,
        "Retailers struggle with delivery app order matching.",
    )

    # cos([1, 0], [0.7, sqrt(0.51)]) = 0.7
    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        lambda _texts: [[1.0, 0.0], [0.7, (0.51 ** 0.5)]],
    )

    result = cluster_submission(db, submission)

    assert result["decision"] == "needs_review"
    assert result["similarity"] == pytest.approx(0.7, abs=0.0001)
    assert db.scalars(select(ClusterEvidence)).all() == []
    assert len(db.scalars(select(ProblemCluster)).all()) == 1


def test_weak_semantic_match_creates_separate_candidate(db, monkeypatch):
    cluster = ProblemCluster(
        title="Existing delivery problem",
        problem_statement="Retailers struggle with delivery reconciliation.",
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    submission = make_analyzed_submission(
        db,
        "Retailers struggle with delivery app order matching.",
        problem_statement="Retailers need help reconciling delivery orders.",
    )

    # Orthogonal vectors have cosine similarity 0.0.
    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        lambda _texts: [[1.0, 0.0], [0.0, 1.0]],
    )

    result = cluster_submission(db, submission)

    assert result["decision"] == "created"
    assert result["cluster_id"] != cluster.id
    assert result["similarity"] is None
    assert len(db.scalars(select(ProblemCluster)).all()) == 2

    evidence = db.scalars(select(ClusterEvidence)).all()
    assert len(evidence) == 1
    assert evidence[0].cluster_id == result["cluster_id"]
    assert evidence[0].is_independent is False

from app.clustering import canonicalize_url, source_duplicate


def test_canonicalize_url_removes_tracking_parameters():
    first = "https://example.com/post/123?utm_source=newsletter&id=7"
    second = "https://example.com/post/123?id=7"

    assert canonicalize_url(first) == canonicalize_url(second)


def test_source_duplicate_detects_exact_text(db):
    original = Submission(
        text="Shop owners struggle to reconcile delivery orders.",
        source_name="source-a",
        analysis_status="pending",
    )
    copy = Submission(
        text="Shop owners struggle to reconcile delivery orders.",
        source_name="source-b",
        analysis_status="pending",
    )
    db.add_all([original, copy])
    db.commit()
    db.refresh(original)
    db.refresh(copy)

    duplicate = source_duplicate(db, copy)

    assert duplicate is not None
    assert duplicate.id == original.id


def test_source_duplicate_detects_canonical_url(db):
    original = Submission(
        text="First version of the complaint.",
        source_name="source-a",
        source_url="https://example.com/post/123?utm_source=newsletter",
        analysis_status="pending",
    )
    copy = Submission(
        text="Reworded version of the complaint.",
        source_name="source-b",
        source_url="https://example.com/post/123",
        analysis_status="pending",
    )
    db.add_all([original, copy])
    db.commit()
    db.refresh(original)
    db.refresh(copy)

    duplicate = source_duplicate(db, copy)

    assert duplicate is not None
    assert duplicate.id == original.id

