from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.services.gemini import (
    PROMPT_VERSION,
    GeminiAnalysisError,
    GeminiNotConfigured,
    GeminiRateLimitError,
    analyze_submission,
)
from app.models import Submission, SubmissionProviderMetadata


def analyze_pending_batch(
    db: Session,
    limit: int,
    *,
    analyzer=analyze_submission,
    before_item=None,
    retry_failed: bool = False,
    submission_ids: list[int] | None = None,
    source_context_by_submission_id: dict[int, str] | None = None,
) -> dict:
    statuses = ["pending", "failed"] if retry_failed else ["pending"]
    query = select(Submission).where(
        Submission.analysis_status.in_(statuses)
    )
    completed_query = (
        select(func.count())
        .select_from(Submission)
        .where(Submission.analysis_status == "completed")
    )
    deferred_query = (
        select(func.count())
        .select_from(Submission)
        .where(Submission.analysis_status.in_(statuses))
    )
    if submission_ids is not None:
        query = query.where(Submission.id.in_(submission_ids))
        completed_query = completed_query.where(
            Submission.id.in_(submission_ids)
        )
        deferred_query = deferred_query.where(
            Submission.id.in_(submission_ids)
        )
    pending = db.scalars(
        query.order_by(Submission.created_at.asc(), Submission.id.asc())
        .limit(limit)
    ).all()
    skipped_completed = db.scalar(completed_query)
    deferred = db.scalar(deferred_query)

    results = {
        "processed": 0,
        "completed": 0,
        "failed": 0,
        "rate_limited": False,
        "provider_not_configured": False,
        "skipped_completed": skipped_completed or 0,
        "deferred": max(0, (deferred or 0) - len(pending)),
        "skipped_claimed_elsewhere": 0,
        "items": [],
    }

    for submission in pending:
        if before_item is not None:
            before_item()
        claimed = db.execute(
            update(Submission)
            .where(
                Submission.id == submission.id,
                Submission.analysis_status.in_(statuses),
            )
            .values(
                analysis_status="processing",
                analysis_error=None,
                analysis_json=None,
                model_name=None,
                prompt_version=None,
                severity_score=None,
                frequency_score=None,
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if claimed.rowcount != 1:
            results["skipped_claimed_elsewhere"] += 1
            continue

        db.refresh(submission)
        submission.analysis_status = "processing"
        submission.analysis_error = None
        submission.analysis_json = None
        submission.model_name = None
        submission.prompt_version = None
        submission.severity_score = None
        submission.frequency_score = None

        try:
            source_context = (
                (source_context_by_submission_id or {}).get(submission.id)
            )
            if not source_context:
                provider_metadata = db.get(
                    SubmissionProviderMetadata,
                    submission.id,
                )
                if provider_metadata is not None:
                    source_context = provider_metadata.metadata_json
            if source_context:
                extraction, model_name = analyzer(
                    submission.text,
                    submission.source_name,
                    submission.language,
                    source_context=source_context,
                )
            else:
                extraction, model_name = analyzer(
                    submission.text,
                    submission.source_name,
                    submission.language,
                )
            submission.analysis_json = extraction.model_dump_json()
            submission.model_name = model_name
            submission.prompt_version = PROMPT_VERSION
            submission.severity_score = float(extraction.urgency_1_to_5)
            submission.frequency_score = float(
                extraction.frequency_signal_1_to_5
            )
            submission.analysis_status = "completed"
            submission.analysis_error = None
            results["completed"] += 1
        except GeminiNotConfigured:
            submission.analysis_status = "pending"
            submission.analysis_error = "provider_not_configured"
            results["provider_not_configured"] = True
            results["configuration_error"] = (
                "The analysis provider is not configured."
            )
            results["items"].append({
                "submission_id": submission.id,
                "status": "pending",
                "error": "provider_not_configured",
            })
            results["processed"] += 1
            db.commit()
            break
        except GeminiRateLimitError:
            submission.analysis_status = "pending"
            submission.analysis_error = "rate_limited"
            results["rate_limited"] = True
            results["items"].append({
                "submission_id": submission.id,
                "status": "pending",
                "error": "rate_limited",
            })
            db.commit()
            results["processed"] += 1
            break
        except GeminiAnalysisError:
            submission.analysis_status = "failed"
            submission.analysis_error = "analysis_failed"
            submission.analysis_json = None
            results["failed"] += 1
        except Exception:
            submission.analysis_status = "failed"
            submission.analysis_error = "analysis_failed"
            submission.analysis_json = None
            results["failed"] += 1
        finally:
            db.commit()

        results["processed"] += 1
        results["items"].append({
            "submission_id": submission.id,
            "status": submission.analysis_status,
            "error": submission.analysis_error,
        })

    return results
