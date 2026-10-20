import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db, initialize_database
from app.main import app, build_opportunity_summary, get_cluster_opportunity
from app.models import (
    ClusterEvidence,
    PipelineRun,
    PipelineRunLock,
    PipelineRunSubmission,
    PipelineSubmissionState,
    ProblemCluster,
    Submission,
    SubmissionProviderMetadata,
)
from app.services.gemini import (
    GeminiAnalysisError,
    GeminiNotConfigured,
    GeminiRateLimitError,
)


@pytest.fixture
def pipeline_env(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    initialize_database(engine)
    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("app.main.engine", engine)
    monkeypatch.setattr(
        "app.security.get_settings",
        lambda: SimpleNamespace(
            service_api_key="",
            submission_rate_limit_per_minute=0,
        ),
    )
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, TestingSession
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _request(sources=("hacker_news",), **overrides):
    payload = {"sources": list(sources)}
    payload.update(overrides)
    return payload


def _valid_analysis(text):
    quote = text[:30]
    data = {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "problem_statement": "Small retailers struggle with manual reporting.",
        "title": "Manual reporting",
        "urgency_1_to_5": 4,
        "frequency_signal_1_to_5": 3,
        "evidence_strength_1_to_5": 4,
        "willingness_to_pay_signal": "none",
        "software_buildability": "likely",
        "evidence_quotes": [quote],
    }
    return SimpleNamespace(
        model_dump_json=lambda: json.dumps(data),
        urgency_1_to_5=4,
        frequency_signal_1_to_5=3,
    )


def _add_submission(db, *, status="pending", text=None):
    text = text or (
        "Shop owners struggle to reconcile delivery orders manually."
    )
    submission = Submission(
        text=text,
        source_name="Hacker News",
        source_url=f"https://example.com/{Submission.__mapper__.class_.__name__}-{id(text)}",
        language="en",
        analysis_status=status,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def _collection_result(submission):
    return {
        "source": "Hacker News",
        "fetched": 1,
        "created": 1,
        "duplicates_skipped": 0,
        "irrelevant_skipped": 0,
        "submission_ids": [submission.id],
    }


def test_pipeline_validates_sources_and_every_bound(pipeline_env):
    client, _ = pipeline_env
    invalid_payloads = [
        {"sources": ["unknown"]},
        {"sources": ["x", "x"]},
        _request(hacker_news_limit=101),
        _request(stack_exchange_limit=101),
        _request(reddit_limit=26),
        _request(x_limit=26),
        _request(x_limit=49),
        _request(x_limit=51),
        _request(analysis_limit=31),
        _request(clustering_limit=31),
        _request(stack_exchange_site="bad/site"),
    ]

    for payload in invalid_payloads:
        assert client.post("/api/pipeline/run", json=payload).status_code == 422


def test_pipeline_runs_all_stages_and_matches_opportunity_endpoint(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    created = {}

    def collect(limit, db):
        if created:
            return {
                "source": "Hacker News",
                "fetched": 1,
                "created": 0,
                "duplicates_skipped": 1,
                "irrelevant_skipped": 0,
                "submission_ids": [],
            }
        submission = _add_submission(db)
        created["id"] = submission.id
        return _collection_result(submission)

    monkeypatch.setattr("app.services.pipeline.collect_hacker_news", collect)
    analyses = []

    def analyze(text, _source, _language):
        analyses.append(text)
        return _valid_analysis(text), "fake-model"

    monkeypatch.setattr(
        "app.main.analyze_submission",
        analyze,
    )

    result = client.post(
        "/api/pipeline/run",
        json=_request(hacker_news_limit=4, analysis_limit=2, clustering_limit=2),
    )

    assert result.status_code == 200
    payload = result.json()
    assert payload["collection"]["hacker_news"]["status"] == "completed"
    assert payload["requested"]["sources"] == ["hacker_news"]
    assert payload["requested"]["limits"]["hacker_news"] == 4
    assert payload["collection"]["hacker_news"]["created"] == 1
    assert payload["analysis"]["completed"] == 1
    assert payload["clustering"]["created"] == 1
    assert payload["affected_cluster_ids"]
    assert len(payload["opportunity_summaries"]) == 1
    summary = payload["opportunity_summaries"][0]
    endpoint_response = client.get(
        f"/api/clusters/{summary['cluster_id']}/opportunity"
    )
    assert endpoint_response.status_code == 200
    endpoint_summary = endpoint_response.json()
    with session_factory() as db:
        state = db.get(PipelineSubmissionState, created["id"])
    assert summary == endpoint_summary
    assert state.clustering_status == "clustered"

    repeated = client.post(
        "/api/pipeline/run",
        json=_request(),
    )
    assert repeated.status_code == 200
    assert repeated.json()["analysis"]["processed"] == 0
    assert repeated.json()["analysis"]["skipped_completed"] == 0
    assert repeated.json()["clustering"]["skipped"] == 0
    assert len(analyses) == 1


def test_pipeline_scopes_work_and_run_inspection_to_collected_records(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        old_pending = _add_submission(
            db,
            text="Older unrelated pending record must stay untouched.",
        )
        cluster = ProblemCluster(
            title="Manual reporting",
            problem_statement="Small retailers struggle with manual reporting.",
        )
        db.add(cluster)
        db.commit()
        old_evidence = _add_submission(
            db,
            status="completed",
            text="A historical report describes manual retail work.",
        )
        old_evidence.analysis_json = json.dumps({
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "problem_statement": "Manual reporting is burdensome.",
            "title": "Manual reporting",
            "urgency_1_to_5": 3,
            "frequency_signal_1_to_5": 3,
            "evidence_strength_1_to_5": 3,
            "willingness_to_pay_signal": "none",
            "evidence_quotes": [old_evidence.text[:30]],
        })
        old_unclustered = _add_submission(
            db,
            status="completed",
            text="A prior completed record should not be clustered by this run.",
        )
        old_unclustered.analysis_json = json.dumps({
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "problem_statement": "A prior completed record.",
            "title": "Prior record",
        })
        db.add(ClusterEvidence(
            cluster_id=cluster.id,
            submission_id=old_evidence.id,
        ))
        db.commit()
        cluster_id = cluster.id

    collected = {}

    def collect(_limit, db):
        submission = _add_submission(
            db,
            text="Small retailers struggle to generate weekly reports.",
        )
        collected["id"] = submission.id
        return _collection_result(submission) | {
            "submission_metadata": {
                str(submission.id): {
                    "is_reply": True,
                    "author": "store_support",
                },
            },
        }

    monkeypatch.setattr("app.services.pipeline.collect_hacker_news", collect)
    analyzer_calls = []

    def analyze(text, _source, _language, **kwargs):
        analyzer_calls.append((text, kwargs))
        return _valid_analysis(text), "fake-model"

    monkeypatch.setattr("app.main.analyze_submission", analyze)

    def attach_to_existing(db, submission, **_kwargs):
        db.add(ClusterEvidence(
            cluster_id=cluster_id,
            submission_id=submission.id,
        ))
        db.commit()
        return {"decision": "matched", "cluster_id": cluster_id}

    monkeypatch.setattr(
        "app.services.pipeline.cluster_submission",
        attach_to_existing,
    )

    response = client.post(
        "/api/pipeline/run",
        json=_request(analysis_limit=10, clustering_limit=10),
    )

    assert response.status_code == 200
    result = response.json()
    run_id = result["run_id"]
    collected_id = collected["id"]
    assert result["status"] == "completed"
    assert result["collected_submission_ids"] == [collected_id]
    assert result["collection"]["hacker_news"]["submission_ids"] == [
        collected_id
    ]
    assert result["analysis"]["completed"] == 1
    assert result["clustering"]["matched"] == 1
    assert [call[0] for call in analyzer_calls] == [
        "Small retailers struggle to generate weekly reports."
    ]
    assert analyzer_calls[0][1]["source_context"] == json.dumps({
        "is_reply": True,
        "author": "store_support",
    })

    with session_factory() as db:
        assert db.get(Submission, old_pending.id).analysis_status == "pending"
        assert db.get(
            Submission,
            old_unclustered.id,
        ).analysis_status == "completed"
        assert db.get(
            PipelineSubmissionState,
            old_unclustered.id,
        ) is None
        assert db.get(PipelineRun, run_id).status == "completed"
        assert db.get(PipelineRunSubmission, (run_id, collected_id))

    run_response = client.get(f"/api/pipeline/runs/{run_id}")
    assert run_response.status_code == 200
    run_payload = run_response.json()
    assert run_payload["collected_submission_ids"] == [collected_id]
    assert [item["submission_id"] for item in run_payload["submissions"]] == [
        collected_id
    ]
    opportunity = run_payload["opportunity_clusters"][0]
    assert opportunity["cluster_id"] == cluster_id
    assert opportunity["run_evidence_count"] == 1
    assert opportunity["total_attached_evidence_count"] == 2
    assert opportunity["contains_evidence_outside_run"] is True

    run_routes = app.openapi()["paths"]
    assert "/api/pipeline/runs" in run_routes
    assert "/api/pipeline/runs/{run_id}" in run_routes
    runs_response = client.get("/api/pipeline/runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert runs["items"][0]["run_id"] == run_id


def test_missing_collector_ids_fail_closed_without_processing_stale_records(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        old_pending = _add_submission(
            db,
            text="An old pending record must not enter this run.",
        )
        old_completed = _add_submission(
            db,
            status="completed",
            text="An old unclustered record must not enter this run.",
        )
        old_completed.analysis_json = json.dumps({
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "problem_statement": "A historical problem.",
            "title": "Historical problem",
        })
        db.commit()

    collected = {}

    def collect_without_ids(_limit, db):
        created = _add_submission(
            db,
            text="A new record whose collector omitted its ID.",
        )
        collected["id"] = created.id
        return {
            "source": "Hacker News",
            "fetched": 1,
            "created": 1,
            "duplicates_skipped": 0,
            "irrelevant_skipped": 0,
        }

    monkeypatch.setattr(
        "app.services.pipeline.collect_hacker_news",
        collect_without_ids,
    )
    analyzer_calls = []
    monkeypatch.setattr(
        "app.main.analyze_submission",
        lambda *args, **kwargs: analyzer_calls.append(args),
    )
    clustering_calls = []
    monkeypatch.setattr(
        "app.services.pipeline.cluster_submission",
        lambda *args, **kwargs: clustering_calls.append(args),
    )

    response = client.post(
        "/api/pipeline/run",
        json=_request(analysis_limit=10, clustering_limit=10),
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "failed"
    assert result["collected_submission_ids"] == []
    assert result["analysis"]["processed"] == 0
    assert result["clustering"]["considered"] == 0
    assert result["collection"]["hacker_news"]["status"] == "failed"
    assert result["collection"]["hacker_news"]["created"] == 1
    assert result["collection"]["hacker_news"]["submission_ids"] == []
    assert result["failures"] == [{
        "stage": "collection",
        "code": "collection_result_invalid",
        "source": "hacker_news",
    }]
    assert analyzer_calls == []
    assert clustering_calls == []

    with session_factory() as db:
        assert db.get(
            Submission,
            old_pending.id,
        ).analysis_status == "pending"
        assert db.get(
            Submission,
            old_completed.id,
        ).analysis_status == "completed"
        assert db.get(
            Submission,
            collected["id"],
        ).analysis_status == "pending"
        assert db.query(PipelineRunSubmission).count() == 0


def test_targeted_analysis_uses_persisted_social_metadata(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        submission = _add_submission(
            db,
            text="I struggle with this manual workflow every week.",
        )
        submission.source_name = "X"
        db.add(SubmissionProviderMetadata(
            submission_id=submission.id,
            source_name="X",
            metadata_json=json.dumps({
                "is_reply": True,
                "author": "support_account",
            }),
        ))
        db.commit()
        submission_id = submission.id

    calls = []

    def analyze(text, source, language, **kwargs):
        calls.append(kwargs.get("source_context"))
        extraction = _valid_analysis(text)
        dump = extraction.model_dump_json
        extraction.model_dump_json = lambda indent=None: json.dumps(
            json.loads(dump()),
            indent=indent,
        )
        return extraction, "fake-model"

    monkeypatch.setattr("app.main.analyze_submission", analyze)

    response = client.post(f"/api/submissions/{submission_id}/analyze")

    assert response.status_code == 200
    assert calls == [json.dumps({
        "is_reply": True,
        "author": "support_account",
    })]


def test_pipeline_marks_limit_deferred_run_as_partial(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    created_ids = []

    def collect(_limit, db):
        for text in (
            "Retailers struggle to reconcile monthly orders manually.",
            "Shop owners struggle to track inventory changes daily.",
        ):
            created_ids.append(_add_submission(db, text=text).id)
        return {
            "source": "Hacker News",
            "fetched": 2,
            "created": 2,
            "duplicates_skipped": 0,
            "irrelevant_skipped": 0,
            "submission_ids": created_ids.copy(),
        }

    monkeypatch.setattr("app.services.pipeline.collect_hacker_news", collect)
    monkeypatch.setattr(
        "app.main.analyze_submission",
        lambda text, *_args: (_valid_analysis(text), "fake-model"),
    )

    def create_cluster(db, submission, **_kwargs):
        cluster = ProblemCluster(
            title=f"Problem {submission.id}",
            problem_statement="A test problem.",
        )
        db.add(cluster)
        db.commit()
        return {"decision": "created", "cluster_id": cluster.id}

    monkeypatch.setattr(
        "app.services.pipeline.cluster_submission",
        create_cluster,
    )

    response = client.post(
        "/api/pipeline/run",
        json=_request(analysis_limit=1, clustering_limit=1),
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "partial"
    assert result["analysis"]["deferred"] == 1
    assert result["clustering"]["created"] == 1
    with session_factory() as db:
        statuses = {
            item.id: item.analysis_status
            for item in db.scalars(
                select(Submission).where(Submission.id.in_(created_ids))
            ).all()
        }
    assert sorted(statuses.values()) == ["completed", "pending"]


def test_pipeline_continues_independent_sources_after_source_failure(
    pipeline_env,
    monkeypatch,
):
    client, _ = pipeline_env
    monkeypatch.setattr(
        "app.services.pipeline.collect_hacker_news",
        lambda *_args: (_ for _ in ()).throw(
            __import__("fastapi").HTTPException(
                status_code=502,
                detail="sanitized source failure",
            )
        ),
    )

    def collect_stack(_limit, _site, db):
        submission = _add_submission(db, text=(
            "Small retailers struggle to reconcile inventory manually."
        ))
        result = _collection_result(submission)
        result["source"] = "Stack Exchange:stackoverflow"
        return result

    monkeypatch.setattr(
        "app.services.pipeline.collect_stack_exchange",
        collect_stack,
    )
    monkeypatch.setattr(
        "app.main.analyze_submission",
        lambda text, *_args: (_valid_analysis(text), "fake-model"),
    )

    response = client.post(
        "/api/pipeline/run",
        json=_request(("hacker_news", "stack_exchange")),
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "partial"
    assert result["collection"]["hacker_news"]["status"] == "failed"
    assert result["collection"]["stack_exchange"]["created"] == 1
    assert result["analysis"]["completed"] == 1
    assert result["clustering"]["created"] == 1
    assert result["failures"] == [{
        "stage": "collection",
        "code": "provider_error",
        "source": "hacker_news",
    }]


def test_pipeline_persists_failed_run_when_all_collectors_fail(
    pipeline_env,
    monkeypatch,
):
    client = pipeline_env[0]
    monkeypatch.setattr(
        "app.services.pipeline.collect_hacker_news",
        lambda *_args: (_ for _ in ()).throw(
            __import__("fastapi").HTTPException(
                status_code=502,
                detail="private provider response",
            )
        ),
    )

    response = client.post("/api/pipeline/run", json=_request())

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "failed"
    assert result["failures"] == [{
        "stage": "collection",
        "code": "provider_error",
        "source": "hacker_news",
    }]
    assert "private provider response" not in response.text
    persisted = client.get(
        f"/api/pipeline/runs/{result['run_id']}"
    ).json()
    assert persisted["status"] == "failed"
    assert persisted["failures"] == result["failures"]


def test_provider_rate_limit_is_reported_and_does_not_block_other_source(
    pipeline_env,
    monkeypatch,
):
    client, _ = pipeline_env
    calls = []

    def limited(*_args):
        calls.append("reddit")
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Provider rate limit reached; retry later.",
        )

    def collect_x(_limit, db):
        calls.append("x")
        submission = _add_submission(
            db,
            text="X users struggle to manage this manual workflow.",
        )
        result = _collection_result(submission)
        result["source"] = "X"
        return result

    monkeypatch.setattr("app.services.pipeline.collect_reddit", limited)
    monkeypatch.setattr("app.services.pipeline.collect_x", collect_x)
    monkeypatch.setattr(
        "app.main.analyze_submission",
        lambda text, *_args: (_valid_analysis(text), "fake-model"),
    )

    response = client.post(
        "/api/pipeline/run",
        json=_request(("reddit", "x")),
    )

    assert response.status_code == 200
    result = response.json()
    assert calls == ["reddit", "x"]
    assert result["collection"]["reddit"]["error_code"] == "rate_limited"
    assert result["collection"]["x"]["created"] == 1
    assert result["analysis"]["completed"] == 1

def test_analysis_rate_limit_preserves_success_and_stops_batch(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    submissions = []

    def collect(_limit, db):
        for text in (
            "Shop owners struggle to reconcile delivery orders manually.",
            "Retailers struggle to track stock changes across locations.",
        ):
            submissions.append(_add_submission(db, text=text))
        return {
            "source": "Hacker News",
            "fetched": 2,
            "created": 2,
            "duplicates_skipped": 0,
            "irrelevant_skipped": 0,
            "submission_ids": [item.id for item in submissions],
        }

    monkeypatch.setattr("app.services.pipeline.collect_hacker_news", collect)
    calls = []

    def analyze(text, *_args):
        calls.append(text)
        if len(calls) == 2:
            raise GeminiRateLimitError("secret/provider detail")
        return _valid_analysis(text), "fake-model"

    monkeypatch.setattr("app.main.analyze_submission", analyze)
    response = client.post(
        "/api/pipeline/run",
        json=_request(analysis_limit=2),
    )

    assert response.status_code == 200
    result = response.json()
    assert result["analysis"]["processed"] == 2
    assert result["analysis"]["completed"] == 1
    assert result["analysis"]["rate_limited"] is True
    assert len(calls) == 2
    assert "secret/provider detail" not in response.text
    with session_factory() as db:
        states = [
            db.get(Submission, item.id).analysis_status
            for item in submissions
        ]
    assert states == ["completed", "pending"]
    assert result["clustering"]["created"] == 1


def test_unconfigured_provider_preserves_prior_analysis_and_stops_batch(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    submissions = []

    def collect(_limit, db):
        for text in (
            "Shop owners struggle to reconcile delivery orders manually.",
            "Retailers struggle to track stock changes across locations.",
        ):
            submissions.append(_add_submission(db, text=text))
        return {
            "source": "Hacker News",
            "fetched": 2,
            "created": 2,
            "duplicates_skipped": 0,
            "irrelevant_skipped": 0,
            "submission_ids": [item.id for item in submissions],
        }

    monkeypatch.setattr("app.services.pipeline.collect_hacker_news", collect)
    calls = []

    def analyze(text, *_args):
        calls.append(text)
        if len(calls) == 2:
            raise GeminiNotConfigured("private configuration detail")
        return _valid_analysis(text), "fake-model"

    monkeypatch.setattr("app.main.analyze_submission", analyze)
    response = client.post(
        "/api/pipeline/run",
        json=_request(analysis_limit=2),
    )

    assert response.status_code == 200
    result = response.json()
    assert result["analysis"]["completed"] == 1
    assert result["analysis"]["provider_not_configured"] is True
    assert len(calls) == 2
    assert "private configuration detail" not in response.text
    with session_factory() as db:
        statuses = [
            db.get(Submission, item.id).analysis_status
            for item in submissions
        ]
    assert statuses == ["completed", "pending"]
    assert result["clustering"]["created"] == 1


def test_analysis_failure_is_recorded_without_discarding_later_success(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    submissions = []

    def collect(_limit, db):
        for text in (
            "Shop owners struggle to reconcile delivery orders manually.",
            "Retailers struggle to track stock changes across locations.",
        ):
            submissions.append(_add_submission(db, text=text))
        return {
            "source": "Hacker News",
            "fetched": 2,
            "created": 2,
            "duplicates_skipped": 0,
            "irrelevant_skipped": 0,
            "submission_ids": [item.id for item in submissions],
        }

    monkeypatch.setattr("app.services.pipeline.collect_hacker_news", collect)
    calls = []

    def analyze(text, *_args):
        calls.append(text)
        if len(calls) == 1:
            raise GeminiAnalysisError("provider detail must not leak")
        return _valid_analysis(text), "fake-model"

    monkeypatch.setattr("app.main.analyze_submission", analyze)
    response = client.post(
        "/api/pipeline/run",
        json=_request(analysis_limit=2),
    )

    assert response.status_code == 200
    result = response.json()
    assert result["analysis"]["failed"] == 1
    assert result["analysis"]["completed"] == 1
    assert len(calls) == 2
    assert "provider detail must not leak" not in response.text
    with session_factory() as db:
        statuses = [
            db.get(Submission, item.id).analysis_status
            for item in submissions
        ]
    assert statuses == ["failed", "completed"]
    assert result["clustering"]["created"] == 1


def test_failed_analysis_is_skipped_then_retried_only_when_requested(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        submission = _add_submission(db)

    monkeypatch.setattr(
        "app.services.pipeline.collect_hacker_news",
        lambda *_args: _collection_result(submission) | {"created": 0},
    )
    calls = []

    def analyze(text, *_args):
        calls.append(1)
        if len(calls) == 1:
            raise GeminiAnalysisError("temporary model error")
        return _valid_analysis(text), "fake-model"

    monkeypatch.setattr("app.main.analyze_submission", analyze)
    failed = client.post("/api/pipeline/run", json=_request())
    skipped = client.post("/api/pipeline/run", json=_request())
    retried = client.post(
        "/api/pipeline/run",
        json=_request(retry_failed_analysis=True),
    )

    assert failed.status_code == skipped.status_code == retried.status_code == 200
    assert failed.json()["status"] == "partial"
    assert failed.json()["analysis"]["failed"] == 1
    assert skipped.json()["analysis"]["processed"] == 0
    assert retried.json()["analysis"]["completed"] == 1
    assert len(calls) == 2
    with session_factory() as db:
        assert db.get(Submission, submission.id).analysis_status == "completed"


def test_pipeline_persists_review_state_skips_and_explicitly_retries(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        submission = _add_submission(db, status="completed")
        submission.analysis_json = json.dumps({
            "problem_statement": "A concrete problem",
            "title": "Concrete problem",
        })
        db.commit()

    monkeypatch.setattr(
        "app.services.pipeline.collect_hacker_news",
        lambda *_args: _collection_result(submission) | {"created": 0},
    )
    calls = []

    def needs_review(_db, _submission, *, retry_needs_review=False):
        calls.append(retry_needs_review)
        return {
            "decision": "needs_review",
            "cluster_id": 7,
            "similarity": 0.7,
        }

    monkeypatch.setattr("app.services.pipeline.cluster_submission", needs_review)
    first = client.post(
        "/api/pipeline/run",
        json=_request(),
    )
    second = client.post(
        "/api/pipeline/run",
        json=_request(),
    )
    retry = client.post(
        "/api/pipeline/run",
        json=_request(retry_needs_review=True),
    )

    assert first.status_code == second.status_code == retry.status_code == 200
    assert first.json()["clustering"]["needs_review"] == 1
    assert second.json()["clustering"]["skipped"] == 1
    assert retry.json()["clustering"]["needs_review"] == 1
    assert calls == [False, True]
    with session_factory() as db:
        state = db.get(PipelineSubmissionState, submission.id)
    assert state.clustering_status == "needs_review"


def test_failed_clustering_is_persisted_skipped_and_explicitly_retried(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        submission = _add_submission(db, status="completed")
        submission.analysis_json = json.dumps({
            "problem_statement": "A concrete problem",
            "title": "Concrete problem",
        })
        cluster = ProblemCluster(
            title="Existing candidate",
            problem_statement="A distinct problem",
        )
        db.add(cluster)
        db.commit()
        cluster_id = cluster.id

    monkeypatch.setattr(
        "app.services.pipeline.collect_hacker_news",
        lambda *_args: _collection_result(submission) | {"created": 0},
    )
    calls = []

    def cluster_attempt(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("embedding provider detail")
        return {"decision": "matched", "cluster_id": cluster_id}

    monkeypatch.setattr(
        "app.services.pipeline.cluster_submission",
        cluster_attempt,
    )
    failed = client.post("/api/pipeline/run", json=_request())
    skipped = client.post("/api/pipeline/run", json=_request())
    retried = client.post(
        "/api/pipeline/run",
        json=_request(retry_failed_clustering=True),
    )

    assert failed.status_code == skipped.status_code == retried.status_code == 200
    assert failed.json()["clustering"]["failed"] == 1
    assert skipped.json()["clustering"]["skipped"] == 1
    assert retried.json()["clustering"]["matched"] == 1
    assert len(calls) == 2
    assert "embedding provider detail" not in failed.text
    with session_factory() as db:
        state = db.get(PipelineSubmissionState, submission.id)
    assert state.clustering_status == "clustered"


def test_pipeline_records_clustering_decision_counts(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        cluster = ProblemCluster(
            title="Existing",
            problem_statement="Existing problem",
        )
        new_cluster = ProblemCluster(
            title="New",
            problem_statement="A newly discovered issue",
        )
        db.add_all([cluster, new_cluster])
        db.commit()
        submissions = [
            _add_submission(db, status="completed", text=f"Problem {i} text is sufficiently descriptive.")
            for i in range(4)
        ]
        for item in submissions:
            item.analysis_json = json.dumps({
                "problem_statement": "A concrete problem",
                "title": "Concrete problem",
            })
        db.commit()
        cluster_id = cluster.id
        new_cluster_id = new_cluster.id

    monkeypatch.setattr(
        "app.services.pipeline.collect_hacker_news",
        lambda *_args: {
            "source": "Hacker News",
            "fetched": len(submissions),
            "created": 0,
            "duplicates_skipped": len(submissions),
            "irrelevant_skipped": 0,
            "submission_ids": [item.id for item in submissions],
        },
    )
    decisions = iter([
        {"decision": "matched", "cluster_id": cluster_id},
        {"decision": "created", "cluster_id": new_cluster_id},
        {"decision": "already_clustered", "cluster_id": cluster_id},
        {
            "decision": "duplicate_evidence",
            "existing_cluster_id": cluster_id,
        },
    ])
    monkeypatch.setattr(
        "app.services.pipeline.cluster_submission",
        lambda *_args, **_kwargs: next(decisions),
    )

    response = client.post(
        "/api/pipeline/run",
        json=_request(clustering_limit=4),
    )

    assert response.status_code == 200
    counts = response.json()["clustering"]
    assert counts["matched"] == 1
    assert counts["created"] == 1
    assert counts["already_clustered"] == 1
    assert counts["duplicates"] == 1
    with session_factory() as db:
        states = db.scalars(select(PipelineSubmissionState)).all()
    assert {item.clustering_status for item in states} == {
        "clustered",
        "duplicate",
    }


def test_pipeline_rejects_concurrent_and_expired_lock_until_offline_recovery(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        db.add(PipelineRunLock(
            id=1,
            owner_token="active",
            started_at=datetime.now(timezone.utc),
            locked_until=datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
        db.commit()

    blocked = client.post("/api/pipeline/run", json=_request())
    assert blocked.status_code == 409

    with session_factory() as db:
        lock = db.get(PipelineRunLock, 1)
        lock.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        pending_recovery = _add_submission(
            db,
            status="processing",
            text="Shop owners struggle to reconcile sales each day.",
        )
        db.commit()

    monkeypatch.setattr(
        "app.services.pipeline.collect_hacker_news",
        lambda *_args: _collection_result(pending_recovery),
    )
    monkeypatch.setattr(
        "app.main.analyze_submission",
        lambda text, *_args: (_valid_analysis(text), "fake-model"),
    )

    expired_without_recovery = client.post(
        "/api/pipeline/run",
        json=_request(),
    )
    rejected_takeover = client.post(
        "/api/pipeline/run",
        json=_request(recover_interrupted_run=True),
    )

    assert expired_without_recovery.status_code == 409
    assert rejected_takeover.status_code == 409
    assert "API-based stale-lock takeover is disabled" in (
        rejected_takeover.json()["detail"]
    )
    status = client.get("/api/pipeline/status").json()
    assert status["run_lock_held"] is True
    assert status["lock_lease_expired"] is True
    assert status["owner_liveness_unknown"] is True

    # Simulate the documented offline procedure after all API workers stop.
    with session_factory() as db:
        lock = db.get(PipelineRunLock, 1)
        lock.owner_token = None
        lock.started_at = None
        lock.locked_until = None
        db.get(Submission, pending_recovery.id).analysis_status = "pending"
        db.commit()
    recovered_after_manual_clear = client.post(
        "/api/pipeline/run",
        json=_request(),
    )
    assert recovered_after_manual_clear.status_code == 200
    with session_factory() as db:
        assert db.get(Submission, pending_recovery.id).analysis_status == "completed"


def test_additive_initialization_preserves_previous_schema_and_rows(tmp_path):
    legacy_engine = create_engine(
        f"sqlite:///{tmp_path / 'legacy.db'}",
        connect_args={"check_same_thread": False},
    )
    for table in (
        Submission.__table__,
        ProblemCluster.__table__,
        ClusterEvidence.__table__,
    ):
        table.create(legacy_engine)
    with legacy_engine.begin() as connection:
        connection.execute(
            Submission.__table__.insert().values(
                id=1,
                text="Previously stored evidence",
                source_name="manual",
                language="en",
                analysis_status="pending",
            )
        )
        connection.execute(
            ProblemCluster.__table__.insert().values(
                id=1,
                title="Legacy cluster",
                problem_statement="A historical problem.",
            )
        )
        connection.execute(
            ClusterEvidence.__table__.insert().values(
                id=1,
                cluster_id=1,
                submission_id=1,
                is_independent=True,
                relationship_type="supports",
            )
        )
    before_columns = {
        table_name: {
            column["name"]
            for column in inspect(legacy_engine).get_columns(table_name)
        }
        for table_name in (
            "submissions",
            "problem_clusters",
            "cluster_evidence",
        )
    }

    initialize_database(legacy_engine)

    with sessionmaker(bind=legacy_engine)() as db:
        existing = db.get(Submission, 1)
        existing_cluster = db.get(ProblemCluster, 1)
        existing_evidence = db.get(ClusterEvidence, 1)
        assert existing.text == "Previously stored evidence"
        assert existing_cluster.title == "Legacy cluster"
        assert existing_evidence.submission_id == 1
        assert db.get(PipelineSubmissionState, 1) is None
        assert db.get(PipelineRunLock, 1) is None
    after_inspection = inspect(legacy_engine)
    after_columns = {
        table_name: {
            column["name"]
            for column in after_inspection.get_columns(table_name)
        }
        for table_name in before_columns
    }
    assert after_columns == before_columns
    assert "pipeline_submission_states" in Base.metadata.tables
    assert "pipeline_run_locks" in Base.metadata.tables
    assert "pipeline_runs" in Base.metadata.tables
    assert "pipeline_run_submissions" in Base.metadata.tables
    assert "submission_provider_metadata" in Base.metadata.tables
    assert set(after_inspection.get_table_names()) >= {
        "source_observations",
        "weekly_ranking_snapshots",
        "weekly_ranking_entries",
    }
    legacy_engine.dispose()


def test_opportunity_summary_core_matches_existing_route(pipeline_env):
    _client, session_factory = pipeline_env
    with session_factory() as db:
        cluster = ProblemCluster(
            title="Test",
            problem_statement="A problem",
        )
        db.add(cluster)
        db.commit()
        first = build_opportunity_summary(cluster.id, db)
        second = get_cluster_opportunity(cluster.id, db)
    assert first == second


def _add_scored_cluster(
    db,
    *,
    title,
    category,
    status,
    text,
    urgency,
):
    cluster = ProblemCluster(
        title=title,
        problem_statement=f"{title} is an ongoing problem for users.",
        category=category,
        status=status,
    )
    submission = Submission(
        text=text,
        source_name="test-source",
        source_url=f"https://example.test/{title.replace(' ', '-')}",
        analysis_status="completed",
        analysis_json=json.dumps({
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "urgency_1_to_5": urgency,
            "frequency_signal_1_to_5": urgency,
            "evidence_strength_1_to_5": urgency,
            "willingness_to_pay_signal": "none",
            "software_buildability": "likely",
            "evidence_quotes": [text[:25]],
        }),
    )
    db.add_all([cluster, submission])
    db.flush()
    db.add(ClusterEvidence(
        cluster_id=cluster.id,
        submission_id=submission.id,
    ))
    db.commit()
    db.refresh(cluster)
    return cluster


def test_opportunities_sort_scores_before_pagination_and_apply_filters(
    pipeline_env,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        low = _add_scored_cluster(
            db,
            title="Low score",
            category="Retail",
            status="candidate",
            text="Retail users struggle with inventory updates every day.",
            urgency=1,
        )
        high = _add_scored_cluster(
            db,
            title="High score",
            category="Retail",
            status="candidate",
            text="Retail users struggle with invoice reconciliation every day.",
            urgency=5,
        )
        middle = _add_scored_cluster(
            db,
            title="Middle score",
            category="Health",
            status="review",
            text="Health clinics struggle with scheduling patients every day.",
            urgency=3,
        )

    first_page = client.get(
        "/api/opportunities",
        params={"limit": 1, "sort_by": "score", "sort_order": "desc"},
    )
    second_page = client.get(
        "/api/opportunities",
        params={"limit": 1, "offset": 1, "sort_by": "score", "sort_order": "desc"},
    )
    filtered = client.get(
        "/api/opportunities",
        params={"category": "Retail", "status": "candidate", "limit": 10},
    )
    score_filtered = client.get(
        "/api/opportunities",
        params={
            "min_score": 60,
            "evidence_level": "single_source_candidate",
            "limit": 10,
        },
    )
    title_sorted = client.get(
        "/api/opportunities",
        params={"sort_by": "title", "sort_order": "asc", "limit": 10},
    )

    assert (
        first_page.status_code
        == second_page.status_code
        == filtered.status_code
        == score_filtered.status_code
        == title_sorted.status_code
        == 200
    )
    assert first_page.json()["total"] == 3
    assert first_page.json()["items"][0]["cluster_id"] == high.id
    assert second_page.json()["items"][0]["cluster_id"] == middle.id
    assert [item["cluster_id"] for item in filtered.json()["items"]] == [
        high.id,
        low.id,
    ]
    assert [
        item["cluster_id"] for item in score_filtered.json()["items"]
    ] == [high.id]
    assert [
        item["title"] for item in title_sorted.json()["items"]
    ] == ["High score", "Low score", "Middle score"]
    assert client.get(
        "/api/opportunities",
        params={"min_score": 90, "max_score": 10},
    ).status_code == 422


def test_opportunity_and_cluster_detail_explain_duplicates_and_provenance(
    pipeline_env,
):
    client, session_factory = pipeline_env
    text = "Retailers struggle to reconcile delivery orders every week."
    analysis = {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "urgency_1_to_5": 4,
        "frequency_signal_1_to_5": 4,
        "evidence_strength_1_to_5": 4,
        "willingness_to_pay_signal": "none",
        "evidence_quotes": [text],
    }
    with session_factory() as db:
        cluster = ProblemCluster(
            title="Delivery reconciliation",
            problem_statement="Retailers struggle to reconcile delivery orders.",
        )
        db.add(cluster)
        db.flush()
        submissions = [
            Submission(
                text=text,
                source_name="reddit",
                source_url="https://example.test/post/1?utm_source=reddit",
                analysis_status="completed",
                analysis_json=json.dumps(analysis),
            ),
            Submission(
                text=f" {text.upper()} ",
                source_name="hacker_news",
                source_url="https://example.test/post/1?utm_medium=copy",
                analysis_status="completed",
                analysis_json=json.dumps(analysis),
            ),
        ]
        db.add_all(submissions)
        db.flush()
        for submission in submissions:
            db.add(ClusterEvidence(
                cluster_id=cluster.id,
                submission_id=submission.id,
            ))
        db.commit()
        cluster_id = cluster.id

    opportunity = client.get(
        f"/api/clusters/{cluster_id}/opportunity"
    ).json()
    detail = client.get(f"/api/clusters/{cluster_id}").json()

    assert opportunity["unique_evidence_count"] == 1
    assert opportunity["duplicate_analyzed_records_excluded"] == 1
    duplicate = opportunity["duplicate_exclusions"][0]
    assert duplicate["duplicate_submission_id"] == submissions[1].id
    assert duplicate["duplicate_of_submission_id"] == submissions[0].id
    assert duplicate["source_name"] == "hacker_news"
    assert duplicate["source_url"] == (
        "https://example.test/post/1?utm_medium=copy"
    )
    assert duplicate["canonical_source_name"] == "reddit"
    assert duplicate["canonical_source_url"] == (
        "https://example.test/post/1?utm_source=reddit"
    )
    assert duplicate["canonical_submission_created_at"] == (
        opportunity["evidence_provenance"][0]["submission_created_at"]
    )
    assert duplicate["reasons"] == ["canonical_url", "exact_text"]
    assert len(opportunity["evidence_provenance"]) == 2
    assert opportunity["evidence_provenance"][0]["source_url"].endswith(
        "?utm_source=reddit"
    )
    assert detail["evidence_count"] == 2
    assert detail["unique_evidence_count"] == 1
    assert detail["duplicate_evidence_count"] == 1
    assert detail["verified_independent_evidence_count"] == 0
    assert detail["opportunity_summary"]["score"] == opportunity["score"]


def test_review_queue_paginates_and_returns_only_safe_error_codes(
    pipeline_env,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        pending = _add_submission(db, status="pending")
        pending.analysis_error = "provider token must not be exposed"
        failed = _add_submission(
            db,
            status="failed",
            text="A failed analysis describes a separate problem.",
        )
        failed.analysis_error = "raw provider response must not be exposed"
        review = _add_submission(
            db,
            status="completed",
            text="A clustering review describes a separate problem.",
        )
        db.add(PipelineSubmissionState(
            submission_id=review.id,
            clustering_status="needs_review",
            cluster_id=17,
            message="private provider detail",
        ))
        db.commit()

    page = client.get("/api/review-queue", params={"limit": 1})
    all_items = client.get("/api/review-queue", params={"limit": 20})

    assert page.status_code == all_items.status_code == 200
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 1
    result = all_items.json()
    assert {item["submission_id"] for item in result["items"]} == {
        pending.id,
        failed.id,
        review.id,
    }
    assert {item["error_code"] for item in result["items"]} == {
        "analysis_pending",
        "analysis_failed",
        "clustering_needs_review",
    }
    serialized = all_items.text
    assert "must not be exposed" not in serialized
    assert "private provider detail" not in serialized
    submission_response = client.get(f"/api/submissions/{failed.id}")
    assert submission_response.json()["analysis_error"] == "analysis_failed"


def test_pipeline_status_reports_persisted_state_without_claiming_history(
    pipeline_env,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        _add_submission(db, status="pending")
        completed = _add_submission(
            db,
            status="completed",
            text="Completed submission has an analysis record for status.",
        )
        db.add(PipelineSubmissionState(
            submission_id=completed.id,
            clustering_status="needs_review",
        ))
        db.commit()

    response = client.get("/api/pipeline/status")
    assert response.status_code == 200
    result = response.json()
    assert result["analysis_status_counts"]["pending"] == 1
    assert result["analysis_status_counts"]["completed"] == 1
    assert result["clustering_status_counts"]["needs_review"] == 1
    assert result["run_history_available"] is True
    assert result["run_lock_held"] is False
    assert "last_run" not in result


def test_completed_analysis_rerun_requires_force_and_failure_clears_old_result(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        submission = _add_submission(db, status="completed")
        submission.analysis_json = json.dumps({"old": "successful result"})
        submission.model_name = "old-model"
        submission.prompt_version = "old-prompt"
        submission.severity_score = 5
        submission.frequency_score = 5
        db.commit()
        submission_id = submission.id

    monkeypatch.setattr(
        "app.main.analyze_submission",
        lambda *_args: (_ for _ in ()).throw(
            GeminiAnalysisError("secret provider detail")
        ),
    )
    refused = client.post(f"/api/submissions/{submission_id}/analyze")
    failed = client.post(
        f"/api/submissions/{submission_id}/analyze",
        params={"force": "true"},
    )

    assert refused.status_code == 409
    assert failed.status_code == 502
    assert "secret provider detail" not in failed.text
    stored = client.get(f"/api/submissions/{submission_id}").json()
    assert stored["analysis_status"] == "failed"
    assert stored["analysis_json"] is None
    assert stored["analysis_error"] == "analysis_failed"


def test_model_rejected_noise_is_not_sent_to_clustering(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        noise = _add_submission(
            db,
            status="completed",
            text="Unrelated promotional content for a new product launch.",
        )
        noise.analysis_json = json.dumps({
            "is_genuine_problem": False,
            "signal_type": "noise",
            "problem_statement": "Promotional content",
            "title": "Promotion",
        })
        db.commit()
        noise_id = noise.id

    monkeypatch.setattr(
        "app.services.pipeline.collect_hacker_news",
        lambda *_args: _collection_result(noise) | {"created": 0},
    )
    calls = []

    def fail_if_called(*_args, **_kwargs):
        calls.append(True)
        return {"decision": "created", "cluster_id": 1}

    monkeypatch.setattr(
        "app.services.pipeline.cluster_submission",
        fail_if_called,
    )

    response = client.post("/api/pipeline/run", json=_request())

    assert response.status_code == 200
    assert calls == []
    assert response.json()["clustering"]["considered"] == 0
    assert response.json()["clustering"]["skipped"] == 1
    with session_factory() as db:
        assert db.get(Submission, noise_id) is not None


def test_is_rejected_analysis_only_rejects_explicit_noise():
    from app.services.pipeline import is_rejected_analysis
    from app.ranking import ACTIONABLE_SIGNAL_TYPES

    assert is_rejected_analysis(
        json.dumps({"is_genuine_problem": False, "signal_type": "noise"})
    )
    assert is_rejected_analysis(
        json.dumps({"is_genuine_problem": True, "signal_type": "noise"})
    )
    assert is_rejected_analysis(
        json.dumps({
            "is_genuine_problem": True,
            "signal_type": "future_unrecognized_type",
        })
    )
    assert is_rejected_analysis(
        json.dumps({
            "is_genuine_problem": True,
            "signal_type": "general_discussion",
        })
    )
    assert not is_rejected_analysis(
        json.dumps({
            "is_genuine_problem": True,
            "signal_type": "pain_point",
        })
    )
    for signal_type in ACTIONABLE_SIGNAL_TYPES:
        assert not is_rejected_analysis(json.dumps({
            "is_genuine_problem": True,
            "signal_type": signal_type,
        }))
    assert not is_rejected_analysis(json.dumps({
        "is_genuine_problem": True,
    }))
    assert is_rejected_analysis(json.dumps({
        "is_genuine_problem": False,
    }))
    assert not is_rejected_analysis(json.dumps({"title": "No flags"}))
    assert not is_rejected_analysis(None)
    assert not is_rejected_analysis("not json")
    assert not is_rejected_analysis("[]")
    assert not is_rejected_analysis(json.dumps({
        "is_genuine_problem": True,
        "signal_type": ["malformed"],
    }))


def test_weekly_ranking_snapshots_are_idempotent_and_trends_are_traceable(
    pipeline_env,
    monkeypatch,
):
    client, session_factory = pipeline_env
    with session_factory() as db:
        lower = _add_scored_cluster(
            db,
            title="Lower initial score",
            category="Retail",
            status="candidate",
            text="Shop owners struggle with weekly invoice reconciliation.",
            urgency=2,
        )
        higher = _add_scored_cluster(
            db,
            title="Higher initial score",
            category="Retail",
            status="candidate",
            text="Retailers struggle with high impact manual inventory work.",
            urgency=4,
        )
        excluded = _add_scored_cluster(
            db,
            title="Not software buildable",
            category="Retail",
            status="candidate",
            text="A physical infrastructure problem is urgent.",
            urgency=5,
        )
        excluded_submission = db.scalars(
            select(Submission)
            .join(ClusterEvidence)
            .where(ClusterEvidence.cluster_id == excluded.id)
        ).one()
        analysis = json.loads(excluded_submission.analysis_json)
        analysis["software_buildability"] = "unlikely"
        excluded_submission.analysis_json = json.dumps(analysis)
        db.commit()

    first_week = date(2026, 10, 19)
    second_week = date(2026, 10, 26)
    monkeypatch.setattr(
        "app.services.rankings.current_week_start",
        lambda: first_week,
    )
    first_response = client.post("/api/rankings/weekly/snapshots")
    repeated_response = client.post("/api/rankings/weekly/snapshots")

    assert first_response.status_code == 200
    first_snapshot = first_response.json()
    assert [item["cluster_id"] for item in first_snapshot["items"]] == [
        higher.id,
        lower.id,
    ]
    assert [item["rank"] for item in first_snapshot["items"]] == [1, 2]
    assert repeated_response.json() == first_snapshot
    assert excluded.id not in {
        item["cluster_id"] for item in first_snapshot["items"]
    }

    with session_factory() as db:
        lower_submission = db.scalars(
            select(Submission)
            .join(ClusterEvidence)
            .where(ClusterEvidence.cluster_id == lower.id)
        ).one()
        analysis = json.loads(lower_submission.analysis_json)
        analysis.update({
            "urgency_1_to_5": 5,
            "frequency_signal_1_to_5": 5,
            "evidence_strength_1_to_5": 5,
        })
        lower_submission.analysis_json = json.dumps(analysis)
        db.commit()

    monkeypatch.setattr(
        "app.services.rankings.current_week_start",
        lambda: second_week,
    )
    second_response = client.post("/api/rankings/weekly/snapshots")
    assert second_response.status_code == 200
    assert second_response.json()["items"][0]["cluster_id"] == lower.id

    history = client.get("/api/rankings/weekly/snapshots?limit=4")
    detail = client.get("/api/rankings/weekly/snapshots/2026-10-19")
    trend = client.get(
        f"/api/rankings/weekly/trends/{lower.id}?weeks=2"
    )
    assert history.status_code == detail.status_code == trend.status_code == 200
    assert history.json()["total"] == 2
    assert detail.json() == first_snapshot
    trend_points = trend.json()["items"]
    assert [point["rank"] for point in trend_points] == [2, 1]
    assert trend_points[1]["rank_change"] == 1
    assert trend_points[1]["score_change"] > 0
