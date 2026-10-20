
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    ClusterEvidence,
    PipelineSubmissionState,
    ProblemCluster,
    Submission,
)
from app.cluster_schemas import EvidenceAttach
from app.clustering import (
    AUTO_MATCH_THRESHOLD,
    cosine_similarity,
    normalize_text,
    cluster_submission,
    get_embeddings,
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
        "software_buildability": "likely",
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


def test_normalize_text_handles_whitespace_punctuation_and_unicode():
    assert normalize_text("  Café—owners\t struggle! ") == normalize_text(
        "CAFE\u0301 owners struggle"
    )
    assert normalize_text("订单　无法同步") == normalize_text("订单 无法同步")
    assert normalize_text("“Owners” struggle!!!") == normalize_text(
        '"owners struggle"'
    )
    assert normalize_text(None) == ""


def test_cosine_similarity_identical_vectors():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


@pytest.mark.parametrize("value", [1e-300, 1e308])
def test_cosine_similarity_handles_extreme_finite_values(value):
    assert cosine_similarity([value, 0.0], [value, 0.0]) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ([1.0], [1.0, 0.0]),
        ([0.0], [1.0]),
        ([float("nan")], [1.0]),
    ],
)
def test_cosine_similarity_rejects_invalid_vectors(left, right):
    with pytest.raises(ValueError):
        cosine_similarity(left, right)


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


def test_malformed_embedding_dimensions_fail_without_creating_evidence(
    db, monkeypatch
):
    db.add(
        ProblemCluster(
            title="Existing delivery problem",
            problem_statement="Retailers struggle to reconcile delivery orders.",
        )
    )
    db.commit()
    submission = make_analyzed_submission(
        db,
        "Shop owners struggle to reconcile delivery orders.",
    )
    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        lambda _texts: [[1.0, 0.0], [1.0]],
    )

    with pytest.raises(RuntimeError, match="inconsistent dimensions"):
        cluster_submission(db, submission)

    assert len(db.scalars(select(ProblemCluster)).all()) == 1
    assert db.scalars(select(ClusterEvidence)).all() == []


def test_embedding_response_with_non_finite_values_is_rejected(monkeypatch):
    class FakeClient:
        class Models:
            @staticmethod
            def embed_content(**_kwargs):
                return type(
                    "Response",
                    (),
                    {
                        "embeddings": [
                            type("Embedding", (), {"values": [float("inf")]})()
                        ]
                    },
                )()

        models = Models()

    monkeypatch.setattr(
        "app.clustering.get_settings",
        lambda: type("Settings", (), {"gemini_api_key": "test-key"})(),
    )
    monkeypatch.setattr(
        "app.clustering.genai.Client",
        lambda **_kwargs: FakeClient(),
    )

    with pytest.raises(RuntimeError, match="non-finite or non-numeric"):
        get_embeddings(["test"])


def test_embedding_provider_error_is_explicit(monkeypatch):
    from google.genai.errors import ServerError

    class FakeClient:
        class Models:
            @staticmethod
            def embed_content(**_kwargs):
                raise ServerError(
                    503,
                    {"error": {"message": "temporary outage"}},
                )

        models = Models()

    monkeypatch.setattr(
        "app.clustering.get_settings",
        lambda: type("Settings", (), {"gemini_api_key": "test-key"})(),
    )
    monkeypatch.setattr(
        "app.clustering.genai.Client",
        lambda **_kwargs: FakeClient(),
    )

    with pytest.raises(RuntimeError, match="HTTP 503"):
        get_embeddings(["test"])


def test_missing_embedding_configuration_fails_before_provider_call(
    monkeypatch,
):
    class Settings:
        gemini_api_key = ""

    monkeypatch.setattr(
        "app.clustering.get_settings",
        lambda: Settings(),
    )
    monkeypatch.setattr(
        "app.clustering.genai.Client",
        lambda **_kwargs: pytest.fail("Provider client should not be created"),
    )

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is missing"):
        get_embeddings(["test"])




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


def test_repeated_clustering_request_does_not_duplicate_evidence(
    db, monkeypatch
):
    cluster = ProblemCluster(
        title="Delivery reconciliation",
        problem_statement="Retailers struggle to reconcile delivery orders.",
    )
    db.add(cluster)
    db.commit()
    submission = make_analyzed_submission(
        db,
        "Shop owners struggle to match delivery app orders.",
    )
    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        lambda _texts: [[1.0, 0.0], [1.0, 0.0]],
    )

    first = cluster_submission(db, submission)
    second = cluster_submission(db, submission)

    assert first["decision"] == "matched"
    assert second["decision"] == "already_clustered"
    assert len(db.scalars(select(ClusterEvidence)).all()) == 1


def test_equal_semantic_matches_choose_lowest_cluster_id(db, monkeypatch):
    first_cluster = ProblemCluster(
        title="First candidate",
        problem_statement="Retailers struggle to reconcile delivery orders.",
    )
    second_cluster = ProblemCluster(
        title="Second candidate",
        problem_statement="Retailers struggle to reconcile delivery orders.",
    )
    db.add_all([first_cluster, second_cluster])
    db.commit()
    db.refresh(first_cluster)
    db.refresh(second_cluster)
    submission = make_analyzed_submission(
        db,
        "Shop owners struggle to reconcile delivery app orders.",
    )
    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        lambda _texts: [
            [1.0, 0.0],
            [1.0, 0.0],
            [1.0, 0.0],
        ],
    )

    result = cluster_submission(db, submission)

    assert result["decision"] == "matched"
    assert result["cluster_id"] == min(first_cluster.id, second_cluster.id)


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
    embedding_calls = []

    def embeddings(_texts):
        embedding_calls.append(1)
        return [[1.0, 0.0], [0.7, (0.51 ** 0.5)]]

    monkeypatch.setattr("app.clustering.get_embeddings", embeddings)

    result = cluster_submission(db, submission)
    repeated = cluster_submission(db, submission)
    explicitly_retried = cluster_submission(
        db,
        submission,
        retry_needs_review=True,
    )

    assert result["decision"] == "needs_review"
    assert result["similarity"] == pytest.approx(0.7, abs=0.0001)
    assert repeated["decision"] == "needs_review"
    assert repeated["title"] == cluster.title
    assert repeated["similarity"] == pytest.approx(0.7, abs=0.0001)
    assert explicitly_retried["decision"] == "needs_review"
    assert len(embedding_calls) == 2
    assert db.scalars(select(ClusterEvidence)).all() == []
    assert len(db.scalars(select(ProblemCluster)).all()) == 1
    state = db.get(PipelineSubmissionState, submission.id)
    assert state.clustering_status == "needs_review"


def test_unrelated_moderate_similarity_creates_new_cluster(db, monkeypatch):
    cluster = ProblemCluster(
        title="Delivery order reconciliation",
        problem_statement="Retailers struggle to reconcile delivery orders.",
        affected_user="Independent shop owners",
        category="Business Operations",
        industry="Retail",
    )
    db.add(cluster)
    db.commit()
    submission = make_analyzed_submission(
        db,
        "Ride-hailing passengers face unexpected waiting charges.",
    )
    similarity = 0.65
    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        lambda _texts: [
            [1.0, 0.0],
            [similarity, (1 - similarity ** 2) ** 0.5],
        ],
    )

    result = cluster_submission(db, submission)

    assert result["decision"] == "created"
    assert result["cluster_id"] != cluster.id
    assert len(db.scalars(select(ProblemCluster)).all()) == 2


def test_related_moderate_similarity_still_requires_review(db, monkeypatch):
    cluster = ProblemCluster(
        title="Delivery order reconciliation",
        problem_statement="Retailers struggle to reconcile delivery orders.",
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    submission = make_analyzed_submission(
        db,
        "Retailers lose time matching delivery orders across apps.",
    )
    similarity = 0.65
    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        lambda _texts: [
            [1.0, 0.0],
            [similarity, (1 - similarity ** 2) ** 0.5],
        ],
    )

    result = cluster_submission(db, submission)

    assert result["decision"] == "needs_review"
    assert result["cluster_id"] == cluster.id
    assert result["similarity"] == pytest.approx(similarity, abs=0.0001)
    assert db.scalars(select(ClusterEvidence)).all() == []


def test_irrelevant_top_candidate_does_not_hide_relevant_review_candidate(
    db, monkeypatch
):
    unrelated_cluster = ProblemCluster(
        title="Ride-hailing waiting charges",
        problem_statement="Passengers face unexpected waiting charges.",
        affected_user="Independent shop owners",
        category="Business Operations",
        industry="Retail",
    )
    relevant_cluster = ProblemCluster(
        title="Delivery order reconciliation",
        problem_statement="Retailers struggle to reconcile delivery orders.",
    )
    db.add_all([unrelated_cluster, relevant_cluster])
    db.commit()
    db.refresh(unrelated_cluster)
    db.refresh(relevant_cluster)
    submission = make_analyzed_submission(
        db,
        "Retailers lose time matching delivery orders across apps.",
    )

    def embeddings(texts):
        similarity_by_text = {
            f"Title: {unrelated_cluster.title}\n"
            f"Problem: {unrelated_cluster.problem_statement}\n"
            "Affected user: Independent shop owners\n"
            "Category: Business Operations\n"
            "Industry: Retail": 0.95,
            f"Title: {relevant_cluster.title}\n"
            f"Problem: {relevant_cluster.problem_statement}\n"
            "Affected user: Unknown\n"
            "Category: Unknown\n"
            "Industry: Unknown": 0.65,
        }
        vectors = [[1.0, 0.0]]
        for text in texts[1:]:
            similarity = similarity_by_text[text]
            vectors.append([
                similarity,
                (1 - similarity ** 2) ** 0.5,
            ])
        return vectors

    monkeypatch.setattr("app.clustering.get_embeddings", embeddings)

    result = cluster_submission(db, submission)

    assert result["decision"] == "needs_review"
    assert result["cluster_id"] == relevant_cluster.id
    assert result["cluster_id"] != unrelated_cluster.id
    assert result["similarity"] == pytest.approx(0.65, abs=0.0001)
    assert db.scalars(select(ClusterEvidence)).all() == []


def test_score_just_below_auto_threshold_does_not_auto_attach(
    db, monkeypatch
):
    cluster = ProblemCluster(
        title="Existing problem",
        problem_statement="Retailers struggle with delivery reconciliation.",
    )
    db.add(cluster)
    db.commit()
    submission = make_analyzed_submission(
        db,
        "Retailers struggle to match delivery orders.",
    )
    similarity = AUTO_MATCH_THRESHOLD - 0.00001
    monkeypatch.setattr(
        "app.clustering.get_embeddings",
        lambda _texts: [
            [1.0, 0.0],
            [similarity, (1 - similarity ** 2) ** 0.5],
        ],
    )

    result = cluster_submission(db, submission)

    assert result["decision"] == "needs_review"
    assert result["similarity"] == round(similarity, 4)
    assert db.scalars(select(ClusterEvidence)).all() == []


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


def test_canonicalize_url_normalizes_host_port_and_fragment():
    assert canonicalize_url(
        "HTTPS://EXAMPLE.COM:443/Post/123?z=2&a=1#comments"
    ) == "https://example.com/Post/123?z=2&a=1"


def test_canonicalize_url_preserves_distinct_resources():
    assert canonicalize_url("https://example.com/post?a=1&b=2") != (
        canonicalize_url("https://example.com/post?b=2&a=1")
    )
    assert canonicalize_url("http://example.com/post") != (
        canonicalize_url("https://example.com/post")
    )
    assert canonicalize_url("https://example.com/post") != (
        canonicalize_url("https://example.com/post/")
    )


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        "ftp://example.com/post",
        "https://example.com:invalid/post",
        "https://bad host.example/post",
        "not a url",
    ],
)
def test_canonicalize_url_rejects_missing_or_invalid_urls(url):
    assert canonicalize_url(url) is None


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


def test_source_duplicate_selects_earliest_of_multiple_matches(db):
    first = Submission(
        text="Owners struggle with order reconciliation.",
        source_name="source-a",
    )
    second = Submission(
        text="OWNERS struggle with order-reconciliation!",
        source_name="source-b",
    )
    candidate = Submission(
        text="Owners struggle with order reconciliation",
        source_name="source-c",
    )
    db.add_all([first, second, candidate])
    db.commit()
    db.refresh(first)
    db.refresh(candidate)

    duplicate = source_duplicate(db, candidate)

    assert duplicate is not None
    assert duplicate.id == first.id


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


def test_distinct_urls_and_distinct_content_are_not_duplicates(db):
    first = Submission(
        text="Shop owners struggle to reconcile payouts.",
        source_name="source-a",
        source_url="https://example.com/post/1",
    )
    second = Submission(
        text="Shop owners struggle to forecast inventory.",
        source_name="source-b",
        source_url="https://example.com/post/2",
    )
    db.add_all([first, second])
    db.commit()

    assert source_duplicate(db, second) is None


def test_linked_copy_is_not_added_even_if_earlier_copy_is_pending(db):
    pending = Submission(
        text="Owners struggle with order reconciliation.",
        source_name="source-a",
        analysis_status="pending",
    )
    db.add(pending)
    db.commit()
    linked_copy = make_analyzed_submission(
        db,
        "Owners struggle with order reconciliation!",
    )
    incoming = make_analyzed_submission(
        db,
        "OWNERS struggle with order reconciliation.",
    )
    cluster = ProblemCluster(
        title="Order reconciliation",
        problem_statement="Owners struggle with order reconciliation.",
    )
    db.add(cluster)
    db.commit()
    db.refresh(pending)
    db.refresh(cluster)
    db.add(
        ClusterEvidence(
            cluster_id=cluster.id,
            submission_id=linked_copy.id,
            is_independent=True,
            relationship_type="supports",
        )
    )
    db.commit()

    result = cluster_submission(db, incoming)

    assert result["decision"] == "duplicate_evidence"
    assert result["duplicate_of_submission_id"] == linked_copy.id
    assert result["existing_cluster_id"] == cluster.id
    assert len(db.scalars(select(ClusterEvidence)).all()) == 1
    assert len(db.scalars(select(ProblemCluster)).all()) == 1


def test_new_evidence_defaults_to_unverified_independence(db):
    cluster = ProblemCluster(
        title="Order reconciliation",
        problem_statement="Owners struggle with order reconciliation.",
    )
    submission = make_analyzed_submission(
        db,
        "Owners struggle with order reconciliation.",
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    evidence = ClusterEvidence(
        cluster_id=cluster.id,
        submission_id=submission.id,
        relationship_type="supports",
    )
    db.add(evidence)
    db.commit()

    assert EvidenceAttach(submission_id=submission.id).is_independent is False
    assert evidence.is_independent is False
