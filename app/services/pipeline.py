from datetime import datetime, timedelta, timezone
from uuid import uuid4

import json
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clustering import cluster_submission
from app.models import (
    ClusterEvidence,
    PipelineRun,
    PipelineRunLock,
    PipelineRunSubmission,
    PipelineSubmissionState,
    Submission,
)
from app.ingestion import collect_hacker_news, collect_stack_exchange
from app.pipeline_schemas import PipelineRunRequest
from app.ranking import ACTIONABLE_SIGNAL_TYPES
from app.social_ingestion import collect_reddit, collect_x
from app.services.analysis import analyze_pending_batch

LOCK_LEASE = timedelta(minutes=10)


class _CollectorResultError(Exception):
    pass


def is_rejected_analysis(analysis_json: str | None) -> bool:
    """Return true only when saved analysis explicitly rejects a post."""
    if not analysis_json:
        return False
    try:
        data = json.loads(analysis_json)
    except (TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("is_genuine_problem") is False:
        return True
    signal_type = data.get("signal_type")
    return isinstance(signal_type, str) and (
        signal_type not in ACTIONABLE_SIGNAL_TYPES
    )


def _ensure_lock_row(db: Session) -> None:
    if db.get(PipelineRunLock, 1) is not None:
        return
    db.add(PipelineRunLock(id=1))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def _acquire_lock(db: Session, *, recover_interrupted_run: bool) -> str:
    _ensure_lock_row(db)
    lock = db.get(PipelineRunLock, 1)
    now = datetime.now(timezone.utc)
    if lock is not None and lock.owner_token is not None:
        lease_expired = (
            lock.locked_until is None
            or lock.locked_until.replace(tzinfo=timezone.utc) < now
        )
        detail = (
            "The pipeline lock lease has expired, but expiration does not "
            "prove the previous process stopped. Stop all API workers and "
            "follow the documented offline lock-recovery procedure before "
            "starting another run."
            if lease_expired
            else "A pipeline run is already in progress."
        )
        if recover_interrupted_run:
            detail = (
                "API-based stale-lock takeover is disabled because it could "
                "create concurrent runs. Stop all API workers and follow "
                "the documented offline lock-recovery procedure."
            )
        raise HTTPException(status_code=409, detail=detail)

    token = str(uuid4())
    result = db.execute(
        update(PipelineRunLock)
        .where(
            PipelineRunLock.id == 1,
            PipelineRunLock.owner_token.is_(None),
        )
        .values(
            owner_token=token,
            started_at=now,
            locked_until=now + LOCK_LEASE,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if result.rowcount != 1:
        raise HTTPException(
            status_code=409,
            detail="A pipeline run is already in progress.",
        )
    return token


def _renew_lock(db: Session, token: str) -> None:
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(PipelineRunLock)
        .where(
            PipelineRunLock.id == 1,
            PipelineRunLock.owner_token == token,
        )
        .values(locked_until=now + LOCK_LEASE)
    )
    db.commit()
    if result.rowcount != 1:
        raise HTTPException(
            status_code=409,
            detail="Pipeline run ownership was lost.",
        )


def _release_lock(db: Session, token: str) -> None:
    db.execute(
        update(PipelineRunLock)
        .where(
            PipelineRunLock.id == 1,
            PipelineRunLock.owner_token == token,
        )
        .values(owner_token=None, started_at=None, locked_until=None)
    )
    db.commit()


def get_pipeline_status(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    lock = db.get(PipelineRunLock, 1)
    locked_until = lock.locked_until if lock else None
    if locked_until is not None and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)

    analysis_status_counts = {
        status: count
        for status, count in db.execute(
            select(
                Submission.analysis_status,
                func.count(Submission.id),
            ).group_by(Submission.analysis_status)
        )
    }
    clustering_status_counts = {
        status: count
        for status, count in db.execute(
            select(
                func.coalesce(
                    PipelineSubmissionState.clustering_status,
                    "not_attempted",
                ),
                func.count(Submission.id),
            )
            .select_from(Submission)
            .outerjoin(
                PipelineSubmissionState,
                PipelineSubmissionState.submission_id == Submission.id,
            )
            .where(Submission.analysis_status == "completed")
            .group_by(PipelineSubmissionState.clustering_status)
        )
    }
    return {
        "run_lock_held": bool(lock and lock.owner_token),
        "lock_lease_expired": bool(
            lock
            and lock.owner_token
            and (
                locked_until is None
                or locked_until < now
            )
        ),
        "owner_liveness_unknown": bool(
            lock
            and lock.owner_token
            and (
                locked_until is None
                or locked_until < now
            )
        ),
        "run_started_at": lock.started_at if lock else None,
        "lease_expires_at": locked_until,
        "analysis_status_counts": analysis_status_counts,
        "clustering_status_counts": clustering_status_counts,
        "run_history_available": True,
    }


def _state_for(
    db: Session,
    submission_id: int,
) -> PipelineSubmissionState:
    state = db.get(PipelineSubmissionState, submission_id)
    if state is None:
        state = PipelineSubmissionState(
            submission_id=submission_id,
            clustering_status="not_attempted",
        )
        db.add(state)
    return state


def _failure(
    stage: str,
    *,
    source: str | None = None,
    entity_id: int | None = None,
    code: str,
) -> dict:
    failure = {"stage": stage, "code": code}
    if source is not None:
        failure["source"] = source
    if entity_id is not None:
        failure["entity_id"] = entity_id
    return failure


def _provider_failure_code(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = str(exc.detail).casefold()
        if exc.status_code == 429 or any(
            marker in detail
            for marker in ("rate limit", "rate-limit", "backoff")
        ):
            return "rate_limited"
        if exc.status_code == 503:
            return "provider_unavailable"
        if exc.status_code == 504:
            return "timeout"
        if exc.status_code >= 500:
            return "provider_error"
    return "internal_error"


def run_pipeline(
    request: PipelineRunRequest,
    db: Session,
    *,
    analyzer,
    summarize_opportunity,
    retry_interrupted_run: bool = False,
) -> dict:
    token = _acquire_lock(
        db,
        recover_interrupted_run=retry_interrupted_run,
    )
    run_record = PipelineRun(
        id=token,
        status="running",
        requested_sources=json.dumps(request.sources),
        stage_counts="{}",
        failures="[]",
    )
    db.add(run_record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        _release_lock(db, token)
        raise
    failures = []
    collectors = {
        "hacker_news": lambda: collect_hacker_news(
            request.hacker_news_limit, db
        ),
        "stack_exchange": lambda: collect_stack_exchange(
            request.stack_exchange_limit,
            request.stack_exchange_site,
            db,
        ),
        "reddit": lambda: (
            collect_reddit(
                request.reddit_limit,
                db,
                request.reddit_subreddits,
            )
            if request.reddit_subreddits
            else collect_reddit(request.reddit_limit, db)
        ),
        "x": lambda: collect_x(request.x_limit, db),
    }
    collection = {}
    collected_submission_ids: set[int] = set()
    analysis = {
        "processed": 0,
        "completed": 0,
        "failed": 0,
        "rate_limited": False,
        "provider_not_configured": False,
        "skipped_completed": 0,
        "deferred": 0,
        "items": [],
    }
    clustering = {
        "considered": 0,
        "matched": 0,
        "created": 0,
        "duplicates": 0,
        "already_clustered": 0,
        "needs_review": 0,
        "failed": 0,
        "skipped": 0,
        "items": [],
    }
    affected_cluster_ids = set()
    summaries = []
    stage_counts = {}
    try:
        for source in request.sources:
            _renew_lock(db, token)
            outcome = None
            try:
                outcome = collectors[source]()
                if not isinstance(outcome, dict):
                    raise _CollectorResultError(
                        "Collector returned an invalid result."
                    )
                ids = outcome.get("submission_ids")
                metadata_by_id = outcome.get("submission_metadata", {})
                if ids is None and outcome.get("created", 0) == 0:
                    ids = []
                if not isinstance(ids, list):
                    raise _CollectorResultError(
                        "Collector omitted valid submission identifiers."
                    )
                if (
                    isinstance(outcome.get("created"), int)
                    and outcome["created"] > len(ids)
                ):
                    raise _CollectorResultError(
                        "Collector created more records than it identified."
                    )
                source_submission_ids = set()
                for submission_id in ids:
                    if (
                        isinstance(submission_id, bool)
                        or not isinstance(submission_id, int)
                    ):
                        raise _CollectorResultError(
                            "Collector returned invalid submission identifiers."
                        )
                    if submission_id in source_submission_ids:
                        continue
                    if db.get(Submission, submission_id) is None:
                        raise _CollectorResultError(
                            "Collector returned an unknown submission identifier."
                        )
                    metadata = metadata_by_id.get(
                        str(submission_id),
                        {},
                    ) if isinstance(metadata_by_id, dict) else {}
                    run_submission = PipelineRunSubmission(
                        run_id=token,
                        submission_id=submission_id,
                        source_name=source,
                        source_metadata=(
                            json.dumps(metadata)
                            if isinstance(metadata, dict) and metadata
                            else None
                        ),
                    )
                    db.add(run_submission)
                    source_submission_ids.add(submission_id)
                db.commit()
                collected_submission_ids.update(source_submission_ids)
                collection[source] = outcome
            except Exception as exc:
                db.rollback()
                code = _provider_failure_code(exc)
                failed_outcome = (
                    outcome.copy() if isinstance(outcome, dict) else {}
                )
                failed_outcome.pop("submission_metadata", None)
                failed_outcome["status"] = "failed"
                failed_outcome["error_code"] = (
                    "collection_result_invalid"
                    if isinstance(exc, _CollectorResultError)
                    else code
                )
                collection[source] = {
                    **failed_outcome,
                    "source": failed_outcome.get("source", source),
                    "submission_ids": [],
                }
                failures.append(
                    _failure(
                        "collection",
                        source=source,
                        code=collection[source]["error_code"],
                    )
                )

        _renew_lock(db, token)
        try:
            run_submissions = db.scalars(
                select(PipelineRunSubmission).where(
                    PipelineRunSubmission.run_id == token
                )
            ).all()
            collected_submission_ids = {
                link.submission_id for link in run_submissions
            }
            source_context_by_submission_id = {
                link.submission_id: link.source_metadata
                for link in run_submissions
                if link.source_metadata
            }
            analysis = analyze_pending_batch(
                db,
                request.analysis_limit,
                analyzer=analyzer,
                before_item=lambda: _renew_lock(db, token),
                retry_failed=request.retry_failed_analysis,
                submission_ids=sorted(collected_submission_ids),
                source_context_by_submission_id=(
                    source_context_by_submission_id
                ),
            )
        except Exception:
            db.rollback()
            analysis = {
                "processed": 0,
                "completed": 0,
                "failed": 0,
                "rate_limited": False,
                "provider_not_configured": False,
                "skipped_completed": 0,
                "deferred": 0,
                "items": [],
            }
            failures.append(_failure("analysis", code="stage_error"))

        if analysis["rate_limited"]:
            failures.append(_failure("analysis", code="rate_limited"))
        if analysis.get("provider_not_configured"):
            failures.append(
                _failure("analysis", code="provider_not_configured")
            )
        if analysis["failed"]:
            failures.append(
                _failure("analysis", code="analysis_failed")
            )

        candidates = db.scalars(
            select(Submission)
            .where(
                Submission.id.in_(
                    select(PipelineRunSubmission.submission_id).where(
                        PipelineRunSubmission.run_id == token
                    )
                ),
                Submission.analysis_status == "completed",
            )
            .order_by(Submission.created_at.asc(), Submission.id.asc())
        ).all()
        eligible = []
        for submission in candidates:
            if is_rejected_analysis(submission.analysis_json):
                # Keep rejected records stored without clustering or embeddings.
                clustering["skipped"] += 1
                continue
            state = db.get(PipelineSubmissionState, submission.id)
            linked = db.scalar(
                select(ClusterEvidence).where(
                    ClusterEvidence.submission_id == submission.id
                )
            )
            if linked is not None:
                if state is None or state.clustering_status != "clustered":
                    state = _state_for(db, submission.id)
                    state.clustering_status = "clustered"
                    state.cluster_id = linked.cluster_id
                    state.message = "Evidence was already attached."
                    db.commit()
                clustering["skipped"] += 1
                continue

            status = state.clustering_status if state else "not_attempted"
            should_retry_review = (
                status == "needs_review" and request.retry_needs_review
            )
            should_retry_failed = (
                status == "failed" and request.retry_failed_clustering
            )
            if status != "not_attempted" and not (
                should_retry_review or should_retry_failed
            ):
                clustering["skipped"] += 1
                continue
            eligible.append(submission)

        for submission in eligible[:request.clustering_limit]:
            _renew_lock(db, token)
            clustering["considered"] += 1
            try:
                current_state = db.get(
                    PipelineSubmissionState,
                    submission.id,
                )
                result = cluster_submission(
                    db,
                    submission,
                    retry_needs_review=bool(
                        current_state is not None
                        and current_state.clustering_status == "needs_review"
                        and request.retry_needs_review
                    ),
                )
                decision = result.get("decision")
                state = _state_for(db, submission.id)
                state.cluster_id = (
                    result.get("cluster_id")
                    or result.get("existing_cluster_id")
                )
                state.message = (
                    f"needs_review:{result['similarity']}"
                    if decision == "needs_review"
                    and result.get("similarity") is not None
                    else decision
                )
                if decision in {"matched", "created", "already_clustered"}:
                    state.clustering_status = "clustered"
                    if decision == "created":
                        clustering["created"] += 1
                    elif decision == "already_clustered":
                        clustering["already_clustered"] += 1
                    else:
                        clustering["matched"] += 1
                    if state.cluster_id is not None:
                        affected_cluster_ids.add(state.cluster_id)
                elif decision == "duplicate_evidence":
                    state.clustering_status = "duplicate"
                    clustering["duplicates"] += 1
                    if state.cluster_id is not None:
                        affected_cluster_ids.add(state.cluster_id)
                elif decision == "needs_review":
                    state.clustering_status = "needs_review"
                    clustering["needs_review"] += 1
                else:
                    raise ValueError("Unexpected clustering outcome.")
                db.commit()
                clustering["items"].append({
                    "submission_id": submission.id,
                    "decision": decision,
                    "cluster_id": state.cluster_id,
                })
            except Exception as exc:
                db.rollback()
                state = _state_for(db, submission.id)
                state.clustering_status = "failed"
                state.cluster_id = None
                state.message = (
                    "embedding_error"
                    if isinstance(exc, RuntimeError)
                    else "clustering_error"
                )
                db.commit()
                clustering["failed"] += 1
                clustering["items"].append({
                    "submission_id": submission.id,
                    "decision": "failed",
                })
                failures.append(
                    _failure(
                        "clustering",
                        entity_id=submission.id,
                        code=state.message,
                    )
                )

        clustering["skipped"] += max(
            0,
            len(eligible) - request.clustering_limit,
        )

        for cluster_id in sorted(affected_cluster_ids):
            try:
                summaries.append(summarize_opportunity(cluster_id, db))
            except Exception:
                db.rollback()
                failures.append(
                    _failure(
                        "opportunity_summary",
                        entity_id=cluster_id,
                        code="summary_error",
                    )
                )

        public_collection = {}
        for source, outcome in collection.items():
            public_collection[source] = {
                key: value
                for key, value in outcome.items()
                if key not in {"submission_metadata"}
            }
            if outcome.get("status") != "failed":
                public_collection[source]["status"] = "completed"
        public_analysis = {
            **{
                key: value
                for key, value in analysis.items()
                if key != "configuration_error"
            },
            "items": [
                {
                    "submission_id": item["submission_id"],
                    "status": item["status"],
                    **(
                        {"error_code": "rate_limited"}
                        if analysis["rate_limited"]
                        and item["status"] == "pending"
                        else {}
                    ),
                    **(
                        {"error_code": "provider_not_configured"}
                        if analysis.get("provider_not_configured")
                        and item["status"] == "pending"
                        else {}
                    ),
                    **(
                        {"error_code": "analysis_failed"}
                        if item["status"] == "failed"
                        else {}
                    ),
                }
                for item in analysis["items"]
            ],
        }
        stage_counts = {
            "collection": {
                source: {
                    key: value
                    for key, value in outcome.items()
                    if key not in {"submission_ids", "submission_metadata"}
                    and isinstance(value, (int, float, bool, str, type(None)))
                }
                for source, outcome in collection.items()
            },
            "analysis": {
                key: analysis.get(key)
                for key in (
                    "processed",
                    "completed",
                    "failed",
                    "skipped_completed",
                    "deferred",
                    "skipped_claimed_elsewhere",
                    "rate_limited",
                    "provider_not_configured",
                )
                if key in analysis
            },
            "clustering": {
                key: clustering[key]
                for key in (
                    "considered",
                    "matched",
                    "created",
                    "duplicates",
                    "already_clustered",
                    "needs_review",
                    "failed",
                    "skipped",
                )
            },
        }
        collection_succeeded = any(
            outcome.get("status") != "failed"
            for outcome in collection.values()
        )
        analysis_deferred = analysis.get("deferred", 0) > 0
        clustering_deferred = (
            len(eligible) > request.clustering_limit
        )
        run_record.status = (
            "failed"
            if failures and not collection_succeeded
            else (
                "partial"
                if failures or analysis_deferred or clustering_deferred
                else "completed"
            )
        )
        run_record.completed_at = datetime.now(timezone.utc)
        run_record.stage_counts = json.dumps(stage_counts)
        run_record.failures = json.dumps(failures)
        db.commit()
        return {
            "run_id": token,
            "status": run_record.status,
            "collected_submission_ids": sorted(collected_submission_ids),
            "requested": {
                "sources": request.sources,
                "limits": {
                    "hacker_news": request.hacker_news_limit,
                    "stack_exchange": request.stack_exchange_limit,
                    "stack_exchange_site": request.stack_exchange_site,
                    "reddit": request.reddit_limit,
                    "x": request.x_limit,
                    "analysis": request.analysis_limit,
                    "clustering": request.clustering_limit,
                },
                "retry_needs_review": request.retry_needs_review,
                "retry_failed_clustering": request.retry_failed_clustering,
                "retry_failed_analysis": request.retry_failed_analysis,
                "recover_interrupted_run": request.recover_interrupted_run,
            },
            "collection": public_collection,
            "analysis": public_analysis,
            "clustering": clustering,
            "affected_cluster_ids": sorted(affected_cluster_ids),
            "opportunity_summaries": summaries,
            "failures": failures,
        }
    except Exception:
        db.rollback()
        failed_run = db.get(PipelineRun, token)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.completed_at = datetime.now(timezone.utc)
            failed_run.stage_counts = json.dumps(stage_counts)
            if not failures:
                failures.append(
                    _failure("pipeline", code="pipeline_error")
                )
            failed_run.failures = json.dumps(failures)
            db.commit()
        raise
    finally:
        _release_lock(db, token)
