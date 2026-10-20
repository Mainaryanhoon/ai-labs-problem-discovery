
import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from unittest.mock import patch

from app.db import Base, get_db
from app.services.gemini import GeminiAnalysisError, GeminiRateLimitError
from app.models import Submission

from app.main import analyze_pending_submissions, app


@pytest.fixture
def client(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestingSession = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
    )

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("app.main.engine", test_engine)
    monkeypatch.setattr(
        "app.security.get_settings",
        lambda: SimpleNamespace(
            service_api_key="",
            submission_rate_limit_per_minute=0,
        ),
    )
    app.dependency_overrides[get_db] = override_get_db
    try:
        # The app's startup hook also uses the isolated in-memory engine.
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "ai-labs-problem-discovery"
    assert response.json()["version"] == app.version
    assert client.get("/openapi.json").status_code == 200


def test_create_and_read_submission(client):
    response = client.post(
        "/api/submissions",
        json={
            "text": (
                "Independent shop owners struggle to reconcile "
                "orders across delivery apps."
            ),
            "source_name": "manual",
            "source_url": None,
            "submitted_by": None,
            "language": "en",
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["id"] > 0
    assert created["analysis_status"] == "pending"

    fetched = client.get(f"/api/submissions/{created['id']}")

    assert fetched.status_code == 200
    assert fetched.json()["text"].startswith(
        "Independent shop owners"
    )

def test_cluster_evidence_workflow(client):
    # 1. Create a problem cluster.
    cluster_response = client.post(
        "/api/clusters",
        json={
            "title": "Delivery order reconciliation",
            "problem_statement": (
                "Independent shop owners struggle to reconcile "
                "orders across multiple delivery applications."
            ),
            "affected_user": "Independent shop owners",
            "category": "Business Operations",
            "industry": "Retail",
        },
    )

    assert cluster_response.status_code == 201
    cluster = cluster_response.json()
    cluster_id = cluster["id"]

    # 2. Create two separate submissions.
    submission_ids = []

    for text in [
        "Shop owners struggle to reconcile orders across delivery apps.",
        "Small retailers find it difficult to match sales from "
        "multiple food delivery platforms.",
    ]:
        response = client.post(
            "/api/submissions",
            json={
                "text": text,
                "source_name": "manual",
                "language": "en",
            },
        )

        assert response.status_code == 201
        submission_ids.append(response.json()["id"])

    # 3. Attach both submissions to the cluster.
    first_evidence = client.post(
        f"/api/clusters/{cluster_id}/evidence",
        json={
            "submission_id": submission_ids[0],
            "is_independent": True,
            "relationship_type": "supports",
            "match_confidence": 0.90,
            "notes": "First test submission.",
        },
    )
    assert first_evidence.status_code == 201

    second_evidence = client.post(
        f"/api/clusters/{cluster_id}/evidence",
        json={
            "submission_id": submission_ids[1],
            "is_independent": False,
            "relationship_type": "supports",
            "match_confidence": 0.85,
            "notes": "Second test submission.",
        },
    )
    assert second_evidence.status_code == 201

    # 4. The same submission cannot be linked twice.
    duplicate_response = client.post(
        f"/api/clusters/{cluster_id}/evidence",
        json={
            "submission_id": submission_ids[0],
            "is_independent": True,
            "relationship_type": "supports",
        },
    )
    assert duplicate_response.status_code == 409

    # 5. Verify cluster evidence counts.
    detail_response = client.get(
        f"/api/clusters/{cluster_id}"
    )

    assert detail_response.status_code == 200
    detail = detail_response.json()

    assert detail["evidence_count"] == 2
    assert detail["independent_evidence_count"] == 1
    assert len(detail["evidence"]) == 2




def test_single_analysis_rate_limit_keeps_submission_pending(client):
    # Create a submission without calling Gemini.
    create_response = client.post(
        "/api/submissions",
        json={
            "text": "Shop owners struggle to reconcile delivery orders.",
            "source_name": "manual",
            "language": "en",
        },
    )
    assert create_response.status_code == 201
    submission_id = create_response.json()["id"]

    # Simulate a quota error; no real API request is made.
    with patch(
        "app.main.analyze_submission",
        side_effect=GeminiRateLimitError("Simulated quota limit"),
    ):
        response = client.post(
            f"/api/submissions/{submission_id}/analyze"
        )

    assert response.status_code == 429

    fetched = client.get(f"/api/submissions/{submission_id}")
    assert fetched.status_code == 200
    assert fetched.json()["analysis_status"] == "pending"


def test_failed_analysis_does_not_mark_submission_completed(client):
    create_response = client.post(
        "/api/submissions",
        json={
            "text": "Shop owners struggle to reconcile delivery orders.",
            "source_name": "manual",
            "language": "en",
        },
    )
    assert create_response.status_code == 201
    submission_id = create_response.json()["id"]

    with patch(
        "app.main.analyze_submission",
        side_effect=GeminiAnalysisError(
            "Gemini response failed schema validation."
        ),
    ):
        response = client.post(
            f"/api/submissions/{submission_id}/analyze"
        )

    assert response.status_code == 502
    fetched = client.get(f"/api/submissions/{submission_id}")
    assert fetched.status_code == 200
    assert fetched.json()["analysis_status"] == "failed"
    assert fetched.json()["analysis_json"] is None


def test_batch_analysis_stops_on_rate_limit(client):
    # Create two pending submissions.
    submission_ids = []

    for text in [
        "Shop owners struggle to reconcile delivery orders.",
        "Small retailers struggle to manage stock across stores.",
    ]:
        response = client.post(
            "/api/submissions",
            json={
                "text": text,
                "source_name": "manual",
                "language": "en",
            },
        )
        assert response.status_code == 201
        submission_ids.append(response.json()["id"])

    # The first Gemini call fails with a quota error.
    # The second submission must not be attempted.
    with patch(
        "app.main.analyze_submission",
        side_effect=GeminiRateLimitError("Simulated quota limit"),
    ) as mocked_analysis:
        response = client.post(
            "/api/submissions/analyze-pending",
            params={"limit": 2},
        )

    assert response.status_code == 200
    result = response.json()

    assert result["rate_limited"] is True
    assert result["processed"] == 1
    assert result["completed"] == 0
    assert mocked_analysis.call_count == 1

    fetched = client.get(
        f"/api/submissions/{submission_ids[0]}"
    )
    assert fetched.json()["analysis_status"] == "pending"


def test_batch_analysis_keeps_success_when_later_analysis_fails():
    submissions = [
        SimpleNamespace(
            id=index,
            text=f"Submission {index} describes a concrete problem.",
            source_name="manual",
            language="en",
            analysis_status="pending",
            analysis_error=None,
            analysis_json=None,
            model_name=None,
            prompt_version=None,
            severity_score=None,
            frequency_score=None,
        )
        for index in (901, 902)
    ]

    class FakeDB:
        commits = 0
        claim_rowcount = 1

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: submissions)

        def scalar(self, _statement):
            return 0

        def get(self, _model, _identity):
            return None

        def commit(self):
            self.commits += 1

        def execute(self, _statement):
            return SimpleNamespace(rowcount=self.claim_rowcount)

        def refresh(self, _item):
            pass

    extraction = SimpleNamespace(
        model_dump_json=lambda: '{"is_genuine_problem": true}',
        urgency_1_to_5=3,
        frequency_signal_1_to_5=2,
    )
    db = FakeDB()

    with patch(
        "app.main.analyze_submission",
        side_effect=[
            (extraction, "test-model"),
            GeminiAnalysisError("Gemini response failed validation."),
        ],
    ):
        result = analyze_pending_submissions(limit=2, db=db)

    assert result["processed"] == 2
    assert result["completed"] == 1
    assert result["failed"] == 1
    assert [item["status"] for item in result["items"]] == [
        "completed",
        "failed",
    ]
    assert submissions[0].analysis_json == '{"is_genuine_problem": true}'
    assert submissions[0].analysis_status == "completed"
    assert submissions[1].analysis_status == "failed"


def test_batch_analysis_skips_record_claimed_by_another_worker():
    from app.services.analysis import analyze_pending_batch

    submission = SimpleNamespace(
        id=903,
        text="Submission is waiting for another worker.",
        source_name="manual",
        language="en",
        analysis_status="pending",
        analysis_error=None,
        analysis_json=None,
        model_name=None,
        prompt_version=None,
        severity_score=None,
        frequency_score=None,
    )

    class FakeDB:
        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [submission])

        def scalar(self, _statement):
            return 1

        def execute(self, _statement):
            return SimpleNamespace(rowcount=0)

        def commit(self):
            pass

    calls = []
    result = analyze_pending_batch(
        FakeDB(),
        1,
        analyzer=lambda *_args: calls.append(1),
    )

    assert result["processed"] == 0
    assert result["skipped_claimed_elsewhere"] == 1
    assert calls == []
