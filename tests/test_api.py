
import pytest
from fastapi.testclient import TestClient

from unittest.mock import patch

from app.services.gemini import GeminiRateLimitError
from app.models import Submission

from app.main import app


@pytest.fixture(scope="module")
def client():
    # Entering the context manager runs FastAPI's startup/lifespan hooks.
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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

