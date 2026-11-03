import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import desc, func, select, update
from sqlalchemy.orm import Session
from app.config import get_settings
from app.db import engine, get_db, initialize_database
from app.models import (
    ClusterEvidence,
    ClusterFeedback,
    PipelineRun,
    PipelineRunSubmission,
    PipelineSubmissionState,
    ProblemCluster,
    SourceObservation,
    SourceRecord,
    Submission,
    SubmissionProviderMetadata,
    SourceRecord,
)
from app.schemas import SubmissionCreate, SubmissionRead
from app.services.gemini import (
    PROMPT_VERSION,
    GeminiAnalysisError,
    GeminiNotConfigured,
    GeminiRateLimitError,
    analyze_submission,
)
from app.clustering import (
    canonicalize_url,
    cluster_submission,
    normalize_text,
)
import json
from typing import Literal
from app.ranking import (
    ACTIONABLE_SIGNAL_TYPES,
    SUPPORTING_RELATIONSHIPS,
)
from app.tatvic_scoring import (
    count_distinct_authors,
    count_records_in_window,
    load_policy_context,
    score_tatvic_opportunity,
)
from app.services.analysis import analyze_pending_batch
from app.pipeline_schemas import PipelineRunRequest
from app.services.pipeline import run_pipeline
from app.ingestion import (
    collect_hacker_news,
    collect_stack_exchange,
    router as ingestion_router,
)
from app.social_ingestion import (
    collect_reddit,
    collect_x,
    router as social_ingestion_router,
)
from app.youtube_ingestion import router as youtube_ingestion_router
from app.article_ingestion import router as article_ingestion_router
from app.docs_ingestion import router as docs_ingestion_router

from app.cluster_schemas import (
    ClusterCreate,
    ClusterDetail,
    ClusterRead,
    EvidenceAttach,
    EvidenceRead,
)
from app.operator_schemas import (
    ClusterFeedbackCreate,
    ClusterFeedbackRead,
    LeadListRead,
    OpportunityListRead,
    OpportunitySummaryRead,
    PipelineRunListRead,
    PipelineRunRead,
    PipelineStatusRead,
    ReviewQueueRead,
)
from app.services.pipeline import get_pipeline_status
from datetime import datetime, timedelta, timezone
from datetime import date
from app.provenance import community_yield
from app.security import api_key_matches, limit_submissions, require_api_key
from app.operator_schemas import (
    WeeklyRankingHistoryRead,
    WeeklyRankingSnapshotRead,
    WeeklyTrendRead,
)
from app.services.rankings import (
    create_monthly_snapshot,
    create_weekly_snapshot,
    get_monthly_snapshot,
    get_weekly_snapshot,
    list_monthly_snapshots,
    list_weekly_snapshots,
    weekly_trend,
)
from app.services.ceo_report import generate_and_email_monthly_report


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _evidence_provenance_rows(
    evidence: list[ClusterEvidence],
    db: Session,
    *,
    scoring_status_by_submission: dict[int, str] | None = None,
    scoring_duplicate_by_submission: dict[int, int] | None = None,
) -> list[dict]:
    submission_ids = [
        item.submission_id
        for item in evidence
    ]
    states = {}
    observations_by_submission_id: dict[int, list[SourceObservation]] = {}
    if submission_ids:
        states = {
            state.submission_id: state
            for state in db.scalars(
                select(PipelineSubmissionState).where(
                    PipelineSubmissionState.submission_id.in_(submission_ids)
                )
            ).all()
        }
        for observation in db.scalars(
            select(SourceObservation)
            .where(SourceObservation.submission_id.in_(submission_ids))
            .order_by(SourceObservation.id.asc())
        ).all():
            observations_by_submission_id.setdefault(
                observation.submission_id,
                [],
            ).append(observation)
    source_records = (
        {
            record.submission_id: record
            for record in db.scalars(
                select(SourceRecord).where(
                    SourceRecord.submission_id.in_(submission_ids)
                )
            ).all()
        }
        if submission_ids
        else {}
    )

    seen_urls: dict[str, int] = {}
    seen_texts: dict[str, int] = {}
    representative_order: dict[int, int] = {}
    rows = []
    for position, link in enumerate(evidence):
        submission = link.submission
        if submission is None:
            continue

        canonical_url = canonicalize_url(submission.source_url)
        normalized_text = normalize_text(submission.text)
        matching_ids = set()
        duplicate_reasons = []
        if canonical_url and canonical_url in seen_urls:
            matching_ids.add(seen_urls[canonical_url])
            duplicate_reasons.append("canonical_url")
        if normalized_text and normalized_text in seen_texts:
            matching_ids.add(seen_texts[normalized_text])
            duplicate_reasons.append("exact_text")

        duplicate_of = (
            min(matching_ids, key=lambda item: representative_order[item])
            if matching_ids
            else None
        )
        representative_id = duplicate_of or submission.id
        representative_order.setdefault(representative_id, position)
        if canonical_url:
            seen_urls.setdefault(canonical_url, representative_id)
        if normalized_text:
            seen_texts.setdefault(normalized_text, representative_id)

        scoring_status = (
            (scoring_status_by_submission or {}).get(
                submission.id,
                "not_scored",
            )
        )
        scoring_duplicate = (
            (scoring_duplicate_by_submission or {}).get(submission.id)
        )
        state = states.get(submission.id)
        source_observations = observations_by_submission_id.get(
            submission.id,
            [],
        )
        if not source_observations:
            source_record = source_records.get(submission.id)
            if source_record is not None:
                source_observations = [{
                    "source_name": source_record.source_name,
                    "source_url": submission.source_url,
                    "native_id": source_record.native_id,
                    "community": source_record.community,
                    "source_created_at": (
                        submission.created_at
                        if source_record.timestamp_origin == "source"
                        else None
                    ),
                    "timestamp_origin": source_record.timestamp_origin,
                    "collected_at": source_record.collected_at,
                }]
        rows.append({
            "evidence_id": link.id,
            "submission_id": submission.id,
            "source_name": submission.source_name,
            "source_url": submission.source_url,
            "created_at": link.created_at,
            "submission_created_at": submission.created_at,
            "analysis_status": submission.analysis_status,
            "clustering_status": (
                state.clustering_status if state is not None else None
            ),
            "relationship_type": link.relationship_type,
            "recorded_independent": bool(link.is_independent),
            "verified_independent": False,
            "duplicate_of_submission_id": (
                scoring_duplicate
                if scoring_status == "duplicate_excluded"
                else duplicate_of
            ),
            "duplicate_reasons": duplicate_reasons,
            "scoring_status": scoring_status,
            "source_observations": [
                (
                    {
                        "source_name": observation.source_name,
                        "source_url": observation.source_url,
                        "native_id": observation.native_id,
                        "community": observation.community,
                        "source_created_at": observation.source_created_at,
                        "timestamp_origin": observation.timestamp_origin,
                        "collected_at": observation.collected_at,
                    }
                    if isinstance(observation, SourceObservation)
                    else observation
                )
                for observation in source_observations
            ],
        })
    return rows


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.leads.models  # noqa: F401 — lead tables
    import app.models  # noqa: F401 — cluster_feedback and core tables

    initialize_database(engine)
    yield


app = FastAPI(
    title="AI Labs Problem Discovery API",
    description="Local-first, evidence-backed problem discovery. Starter vertical slice.",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(require_api_key)],
)


@app.middleware("http")
async def protect_api_documentation(request: Request, call_next):
    """Apply the optional API key to FastAPI's non-API documentation routes."""
    documentation_paths = {
        path
        for path in (
            app.docs_url,
            app.redoc_url,
            app.openapi_url,
            app.swagger_ui_oauth2_redirect_url,
        )
        if path
    }
    expected = get_settings().service_api_key
    if (
        expected
        and request.url.path in documentation_paths
        and not api_key_matches(request.headers.get("x-api-key"), expected)
    ):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key."},
        )
    return await call_next(request)


app.include_router(ingestion_router)
app.include_router(social_ingestion_router)
app.include_router(youtube_ingestion_router)
app.include_router(docs_ingestion_router)
app.include_router(article_ingestion_router)




@app.get("/")
def root():
    """API has no UI at /. Use /health, /docs, or the Vite frontend."""
    return {
        "service": "ai-labs-problem-discovery",
        "status": "ok",
        "health": "/health",
        "docs": "/docs",
        "frontend": "http://localhost:5173",
        "note": (
            "This is the API only. Open the Vite app for the dashboard, "
            "or /docs for interactive endpoints."
        ),
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "ai-labs-problem-discovery", "version": app.version}


@app.post(
    "/api/submissions",
    response_model=SubmissionRead,
    status_code=201,
    dependencies=[Depends(limit_submissions)],
)
def create_submission(payload: SubmissionCreate, db: Session = Depends(get_db)):
    item = Submission(
        text=payload.text.strip(),
        source_name=payload.source_name.strip(),
        source_url=str(payload.source_url) if payload.source_url else None,
        submitted_by=payload.submitted_by,
        language=payload.language,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.get("/api/submissions", response_model=list[SubmissionRead])
def list_submissions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(Submission).order_by(desc(Submission.created_at)).offset(offset).limit(limit)
    ).all()


@app.get("/api/submissions/{submission_id}", response_model=SubmissionRead)
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    item = db.get(Submission, submission_id)
    if not item:
        raise HTTPException(status_code=404, detail="Submission not found.")
    return item


@app.post("/api/submissions/{submission_id}/analyze", response_model=SubmissionRead)
def analyze_one_submission(
    submission_id: int,
    force: bool = Query(
        default=False,
        description="Required to replace an existing completed analysis.",
    ),
    db: Session = Depends(get_db),
):
    item = db.get(Submission, submission_id)
    if not item:
        raise HTTPException(status_code=404, detail="Submission not found.")

    if item.analysis_status == "processing":
        raise HTTPException(
            status_code=409,
            detail="This submission is already being analyzed.",
        )
    if item.analysis_status == "completed" and not force:
        raise HTTPException(
            status_code=409,
            detail="A completed analysis exists; set force=true to replace it.",
        )

    claim = update(Submission).where(
        Submission.id == submission_id,
        Submission.analysis_status != "processing",
    )
    if not force:
        claim = claim.where(Submission.analysis_status != "completed")
    claimed = db.execute(
        claim.values(
            analysis_status="processing",
            analysis_error=None,
            analysis_json=None,
            model_name=None,
            prompt_version=None,
            severity_score=None,
            frequency_score=None,
        ).execution_options(synchronize_session=False)
    )
    db.commit()
    if claimed.rowcount != 1:
        if db.get(Submission, submission_id) is None:
            raise HTTPException(status_code=404, detail="Submission not found.")
        raise HTTPException(
            status_code=409,
            detail="This submission cannot be claimed for analysis.",
        )
    db.refresh(item)

    try:
        source_metadata = db.get(
            SubmissionProviderMetadata,
            submission_id,
        )
        if source_metadata is not None:
            result, model_name = analyze_submission(
                item.text,
                item.source_name,
                item.language,
                source_context=source_metadata.metadata_json,
            )
        else:
            result, model_name = analyze_submission(
                item.text,
                item.source_name,
                item.language,
            )
        item.analysis_json = result.model_dump_json(indent=2)
        item.model_name = model_name
        item.prompt_version = PROMPT_VERSION
        item.severity_score = float(result.urgency_1_to_5)
        item.frequency_score = float(result.frequency_signal_1_to_5)
        item.analysis_status = "completed"
        item.analysis_error = None
        db.commit()
        db.refresh(item)
        return item
    except GeminiNotConfigured as exc:
        item.analysis_status = "pending"
        item.analysis_error = "provider_not_configured"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="The analysis provider is not configured.",
        ) from exc
    
    except GeminiRateLimitError as exc:
        item.analysis_status = "pending"
        item.analysis_error = "rate_limited"
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="The analysis provider rate limit was reached.",
        ) from exc
    
    except GeminiAnalysisError as exc:
        logger.warning(
            "Analysis failed for submission_id=%s (%s).",
            submission_id,
            type(exc).__name__,
        )
        item.analysis_status = "failed"
        item.analysis_error = "analysis_failed"
        db.commit()
        raise HTTPException(
            status_code=502,
            detail="The submission analysis failed.",
        ) from exc

    except Exception as exc:
        logger.warning(
            "Unexpected analysis failure for submission_id=%s (%s).",
            submission_id,
            type(exc).__name__,
        )
        item.analysis_status = "failed"
        item.analysis_json = None
        item.analysis_error = "analysis_failed"
        item.model_name = None
        item.prompt_version = None
        item.severity_score = None
        item.frequency_score = None
        db.commit()
        raise HTTPException(
            status_code=502,
            detail="The submission analysis failed.",
        ) from exc

    
@app.post("/api/clusters", response_model=ClusterRead, status_code=201)
def create_cluster(
    payload: ClusterCreate,
    db: Session = Depends(get_db),
):
    cluster = ProblemCluster(
        title=payload.title.strip(),
        problem_statement=payload.problem_statement.strip(),
        affected_user=payload.affected_user,
        category=payload.category,
        industry=payload.industry,
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


@app.get("/api/clusters", response_model=list[ClusterRead])
def list_clusters(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(ProblemCluster)
        .order_by(desc(ProblemCluster.updated_at))
        .offset(offset)
        .limit(limit)
    ).all()


@app.get("/api/policy/tatvic")
def get_tatvic_policy():
    from app.tatvic_scoring import WEIGHT_VERSION

    return {
        "weight_version": WEIGHT_VERSION,
        **load_policy_context(),
    }


@app.get("/api/opportunities", response_model=OpportunityListRead)
def list_opportunities(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None, min_length=1, max_length=100),
    status: str | None = Query(default=None, min_length=1, max_length=30),
    evidence_level: str | None = Query(
        default=None,
        min_length=1,
        max_length=50,
    ),
    min_score: float | None = Query(default=None, ge=0, le=100),
    max_score: float | None = Query(default=None, ge=0, le=100),
    product_ready_only: bool = Query(
        default=True,
        description=(
            "When true (default), only ASSETS.md productizable opportunities "
            "with KEEP_DEMAND evidence and a clear offering angle are listed."
        ),
    ),
    sort_by: Literal[
        "score",
        "title",
        "evidence_level",
        "created_at",
        "updated_at",
    ] = Query(default="score"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    db: Session = Depends(get_db),
):
    if min_score is not None and max_score is not None and min_score > max_score:
        raise HTTPException(
            status_code=422,
            detail="min_score must not exceed max_score.",
        )

    statement = select(ProblemCluster)
    if category is not None:
        statement = statement.where(ProblemCluster.category == category)
    if status is not None:
        statement = statement.where(ProblemCluster.status == status)
    else:
        statement = statement.where(ProblemCluster.status != "rejected")
    clusters = db.scalars(
        statement.order_by(ProblemCluster.id.asc())
    ).all()

    summaries = [
        build_opportunity_summary(cluster.id, db)
        for cluster in clusters
    ]
    if product_ready_only:
        summaries = [
            item
            for item in summaries
            if item.get("product_ready") is True and item.get("score") is not None
        ]
    if evidence_level is not None:
        summaries = [
            item
            for item in summaries
            if item["evidence_level"] == evidence_level
        ]
    if min_score is not None:
        summaries = [
            item
            for item in summaries
            if item["score"] is not None and item["score"] >= min_score
        ]
    if max_score is not None:
        summaries = [
            item
            for item in summaries
            if item["score"] is not None and item["score"] <= max_score
        ]

    summaries.sort(key=lambda item: item["cluster_id"])
    reverse = sort_order == "desc"
    if sort_by == "score":
        scored = [item for item in summaries if item["score"] is not None]
        unscored = [item for item in summaries if item["score"] is None]
        scored.sort(key=lambda item: item["score"], reverse=reverse)
        summaries = scored + unscored
    else:
        sort_keys = {
            "title": lambda item: item["title"].casefold(),
            "evidence_level": lambda item: item["evidence_level"],
            "created_at": lambda item: item["created_at"],
            "updated_at": lambda item: item["updated_at"],
        }
        summaries.sort(key=sort_keys[sort_by], reverse=reverse)

    total = len(summaries)
    return {
        "items": summaries[offset:offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _lead_dashboard_row(lead) -> dict:
    from app.leads.models import Lead

    if not isinstance(lead, Lead):
        raise TypeError("expected Lead")
    quotes = json.loads(lead.evidence_quotes_json or "[]")
    if not isinstance(quotes, list):
        quotes = []
    citations = [q for q in quotes if isinstance(q, str) and q.strip()]
    extra = json.loads(lead.corroborating_urls_json or "[]")
    if not isinstance(extra, list):
        extra = []
    corroborating = [
        url.strip()
        for url in extra
        if isinstance(url, str) and url.strip().startswith(("http://", "https://"))
    ]
    delivery = (lead.delivery_type or "unclear").casefold()
    if delivery not in {"service", "product", "unclear"}:
        delivery = "unclear"
    source_url = lead.source_url if isinstance(lead.source_url, str) else None
    if source_url and not source_url.startswith(("http://", "https://")):
        source_url = None
    return {
        "cluster_id": lead.id,
        "lead_id": lead.id,
        "title": lead.title,
        "category": lead.company,
        "status": lead.status,
        "created_at": lead.first_seen,
        "score": lead.score,
        "evidence_level": "lead",
        "why_now": lead.why_now,
        "first_customer_reach_strategy": lead.first_customer_route,
        "delivery_type": delivery,
        "problem_statement": lead.problem,
        "smallest_first_version": lead.smallest_first_version,
        "source_url": source_url,
        "corroborating_urls": corroborating,
        "evidence_quotes": citations[:3],
        "raw_attached_record_count": 1,
        "unique_evidence_count": max(1, len(citations)),
        "evidence_citations": citations[:3],
    }


@app.get("/api/leads", response_model=LeadListRead)
def list_leads(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    from app.leads.models import Lead

    active = Lead.status != "rejected"
    rows = db.scalars(
        select(Lead)
        .where(active)
        .order_by(Lead.score.desc(), Lead.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    total = db.scalar(select(func.count()).select_from(Lead).where(active)) or 0
    return {
        "items": [_lead_dashboard_row(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@app.post(
    "/api/clusters/{cluster_id}/feedback",
    response_model=ClusterFeedbackRead,
    status_code=201,
)
def record_cluster_feedback(
    cluster_id: int,
    payload: ClusterFeedbackCreate,
    db: Session = Depends(get_db),
):
    from app.leads.models import Lead
    from app.leads.service import record_feedback

    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A non-empty reason is required.")

    lead = db.get(Lead, cluster_id)
    if lead is not None:
        lead_id = lead.id
        try:
            record_feedback(
                db,
                lead_id,
                payload.decision,
                reason,
                payload.reviewer or "ui",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "cluster_id": cluster_id,
            "lead_id": lead_id,
            "decision": payload.decision,
            "reason": reason,
            "reviewer": payload.reviewer or "ui",
            "recorded": True,
        }

    cluster = db.get(ProblemCluster, cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=404,
            detail="Lead or problem cluster not found.",
        )

    cluster.status = payload.decision
    db.add(
        ClusterFeedback(
            cluster_id=cluster_id,
            decision=payload.decision,
            reason=reason,
            reviewer=payload.reviewer or "ui",
        )
    )
    db.commit()
    return {
        "cluster_id": cluster_id,
        "lead_id": None,
        "decision": payload.decision,
        "reason": reason,
        "reviewer": payload.reviewer or "ui",
        "recorded": True,
    }


@app.post(
    "/api/rankings/weekly/snapshots",
    response_model=WeeklyRankingSnapshotRead,
)
def create_weekly_ranking_snapshot(db: Session = Depends(get_db)):
    return create_weekly_snapshot(db, build_opportunity_summary)


@app.get(
    "/api/rankings/weekly/snapshots",
    response_model=WeeklyRankingHistoryRead,
)
def list_weekly_ranking_snapshots(
    limit: int = Query(default=12, ge=1, le=104),
    db: Session = Depends(get_db),
):
    return list_weekly_snapshots(db, limit=limit)


@app.get(
    "/api/rankings/weekly/snapshots/{week_start}",
    response_model=WeeklyRankingSnapshotRead,
)
def read_weekly_ranking_snapshot(
    week_start: date,
    db: Session = Depends(get_db),
):
    snapshot = get_weekly_snapshot(db, week_start)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Ranking snapshot not found.")
    return snapshot


@app.get(
    "/api/rankings/weekly/trends/{cluster_id}",
    response_model=WeeklyTrendRead,
)
def read_weekly_ranking_trend(
    cluster_id: int,
    weeks: int = Query(default=12, ge=1, le=104),
    db: Session = Depends(get_db),
):
    if db.get(ProblemCluster, cluster_id) is None:
        raise HTTPException(status_code=404, detail="Problem cluster not found.")
    return weekly_trend(db, cluster_id, weeks=weeks)


@app.post("/api/rankings/monthly/snapshots")
def create_monthly_ranking_snapshot(
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    return create_monthly_snapshot(
        db,
        build_opportunity_summary,
        force=force,
    )


@app.get("/api/rankings/monthly/snapshots")
def list_monthly_ranking_snapshots(
    limit: int = Query(default=12, ge=1, le=36),
    db: Session = Depends(get_db),
):
    return list_monthly_snapshots(db, limit=limit)


@app.get("/api/rankings/monthly/snapshots/{month_start}")
def read_monthly_ranking_snapshot(
    month_start: date,
    db: Session = Depends(get_db),
):
    snapshot = get_monthly_snapshot(db, month_start)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Monthly snapshot not found.")
    return snapshot


@app.post("/api/reports/monthly/ceo")
def generate_monthly_ceo_report(
    send_email: bool = Query(default=True),
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Primary executive deliverable: Top-6 report (+ optional Gmail API email)."""
    return generate_and_email_monthly_report(
        db,
        build_opportunity_summary,
        send_email=send_email,
        force=force,
    )


@app.get("/api/clusters/{cluster_id}", response_model=ClusterDetail)
def get_cluster(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    cluster = db.get(ProblemCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Problem cluster not found.")

    evidence = db.scalars(
        select(ClusterEvidence)
        .where(ClusterEvidence.cluster_id == cluster_id)
        .order_by(
            ClusterEvidence.created_at.asc(),
            ClusterEvidence.id.asc(),
        )
    ).all()
    provenance = _evidence_provenance_rows(evidence, db)
    independent_count = sum(
        item["recorded_independent"]
        for item in provenance
        if item["duplicate_of_submission_id"] is None
    )
    return {
        "id": cluster.id,
        "title": cluster.title,
        "problem_statement": cluster.problem_statement,
        "affected_user": cluster.affected_user,
        "category": cluster.category,
        "industry": cluster.industry,
        "status": cluster.status,
        "created_at": cluster.created_at,
        "updated_at": cluster.updated_at,
        "evidence_count": len(evidence),
        "independent_evidence_count": independent_count,
        "recorded_independent_evidence_count": independent_count,
        "unique_evidence_count": sum(
            item["duplicate_of_submission_id"] is None
            for item in provenance
        ),
        "duplicate_evidence_count": sum(
            item["duplicate_of_submission_id"] is not None
            for item in provenance
        ),
        "verified_independent_evidence_count": 0,
        "evidence": evidence,
        "evidence_provenance": provenance,
        "opportunity_summary": build_opportunity_summary(cluster_id, db),
    }

def build_opportunity_summary(
    cluster_id: int,
    db: Session,
):
    cluster = db.get(ProblemCluster, cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=404,
            detail="Problem cluster not found.",
        )

    evidence = db.scalars(
        select(ClusterEvidence)
        .where(ClusterEvidence.cluster_id == cluster_id)
        .order_by(
            ClusterEvidence.created_at.asc(),
            ClusterEvidence.id.asc(),
        )
    ).all()
    raw_attached_record_count = len(evidence)
    analyzed_record_count = 0
    analyzed_records = []
    distinct_source_records = set()
    eligible_distinct_source_records = set()
    recorded_independent_records = set()
    source_platforms = set()

    for link in evidence:
        submission = link.submission
        if (
            submission is None
            or submission.analysis_status != "completed"
            or not submission.analysis_json
        ):
            continue
        try:
            analysis = json.loads(submission.analysis_json)
        except (TypeError, json.JSONDecodeError):
            continue

        if not isinstance(analysis, dict):
            continue

        analyzed_record_count += 1
        analyzed_records.append((link, submission, analysis))

    analyzed_provenance = _evidence_provenance_rows(
        [record[0] for record in analyzed_records],
        db,
    )
    unique_records = []
    duplicate_exclusions = []
    scoring_status_by_submission = {}
    scoring_duplicate_by_submission = {}
    analyzed_submission_by_id = {
        submission.id: submission
        for _link, submission, _analysis in analyzed_records
    }
    for (link, submission, analysis), provenance in zip(
        analyzed_records,
        analyzed_provenance,
    ):
        if provenance["duplicate_of_submission_id"] is not None:
            scoring_status_by_submission[submission.id] = (
                "duplicate_excluded"
            )
            scoring_duplicate_by_submission[submission.id] = (
                provenance["duplicate_of_submission_id"]
            )
            canonical_submission = analyzed_submission_by_id[
                provenance["duplicate_of_submission_id"]
            ]
            duplicate_exclusions.append({
                "duplicate_submission_id": submission.id,
                "duplicate_of_submission_id": (
                    provenance["duplicate_of_submission_id"]
                ),
                "source_name": submission.source_name,
                "source_url": submission.source_url,
                "canonical_source_name": canonical_submission.source_name,
                "canonical_source_url": canonical_submission.source_url,
                "canonical_submission_created_at": (
                    canonical_submission.created_at
                ),
                "reasons": provenance["duplicate_reasons"],
            })
            continue

        scoring_status_by_submission[submission.id] = (
            "included"
            if (
                link.relationship_type in SUPPORTING_RELATIONSHIPS
                and analysis.get("is_genuine_problem") is True
                and isinstance(analysis.get("signal_type"), str)
                and analysis.get("signal_type") in ACTIONABLE_SIGNAL_TYPES
            )
            else "not_scored"
        )
        canonical_url = canonicalize_url(submission.source_url)
        normalized_text = normalize_text(submission.text)
        source_record = canonical_url or normalized_text or None
        unique_records.append((link, submission, analysis, source_record))
        source_platforms.add(submission.source_name)
        if source_record is not None:
            distinct_source_records.add(source_record)
            if link.is_independent:
                recorded_independent_records.add(source_record)

    evidence_provenance = _evidence_provenance_rows(
        evidence,
        db,
        scoring_status_by_submission=scoring_status_by_submission,
        scoring_duplicate_by_submission=scoring_duplicate_by_submission,
    )
    duplicate_analyzed_records_excluded = len(duplicate_exclusions)

    if not unique_records:
        return {
            "cluster_id": cluster.id,
            "title": cluster.title,
            "category": cluster.category,
            "status": cluster.status,
            "created_at": cluster.created_at,
            "updated_at": cluster.updated_at,
            "score": None,
            "evidence_level": "insufficient_analyzed_evidence",
            "weight_version": None,
            "section": None,
            "score_components": {},
            "score_component_contributions": {},
            "top10_eligible": False,
            "product_ready": False,
            "single_source_capped": False,
            "offering_angle": "undetermined",
            "smallest_first_version": None,
            "first_client_type": None,
            "why_now": None,
            "first_customer_reach_strategy": None,
            "delivery_type": "unclear",
            "failure_risk_line": None,
            "demand_metrics": {},
            "supporting_capability_evidence_count": 0,
            "role_blocked_keep_demand": [],
            "commercial_qualification": {
                "india_relevance": "none",
                "company_level_available": False,
                "is_commercial_lead": False,
                "is_validated_problem_opportunity": False,
            },
            "commercial_score_v02": {
                "score": None,
                "weight_version": "v0.2-commercial",
                "active_for_ranking": False,
            },
            "raw_attached_record_count": raw_attached_record_count,
            "analyzed_submission_count": analyzed_record_count,
            "unique_evidence_count": len(unique_records),
            "duplicate_analyzed_records_excluded": (
                duplicate_analyzed_records_excluded
            ),
            "duplicate_exclusions": duplicate_exclusions,
            "distinct_source_records": 0,
            "eligible_distinct_source_record_count": 0,
            "unique_source_platform_count": len(source_platforms),
            "unique_demand_platform_count": 0,
            "recorded_independent_records": 0,
            "verified_independent_records": 0,
            "verified_independent_source_count": 0,
            "eligible_evidence_count": 0,
            "commercial_signal": "unknown",
            "software_buildability": "unclear",
            "software_buildability_reasons": [],
            "eligibility": "review_required",
            "source_timestamped_records_7d": 0,
            "commercial_signal_limitation": (
                "No eligible analyzed evidence is available."
            ),
            "reasons": [],
            "limitations": [
                "No valid completed analyses are attached to this cluster.",
                "A score cannot be calculated until evidence is analyzed.",
                "Exact-text and canonical-URL duplicates are excluded from scoring.",
                "Unique source platforms do not prove independent customers or demand.",
                "No independent-source verification mechanism is implemented.",
            ],
            "evidence_provenance": evidence_provenance,
        }

    # Non-supporting and unknown relationship types stay visible in provenance
    # and counts but cannot affect scores or commercial signals.
    # Source role (SOURCES.md) governs demand floor — not the model KEEP label.
    from app.source_roles import (
        is_capability_or_context_role,
        is_demand_role,
        is_market_lead_role,
        role_diagnostics,
        source_role,
    )
    from app.tatvic_scoring import (
        KEEP_CODES_FOR_DEMAND,
        KEEP_CODES_FOR_MARKET_LEAD,
        is_non_product_problem,
    )

    feed_by_submission_id: dict[int, str] = {}
    observation_submission_ids = [submission.id for _, submission, _, _ in unique_records]
    if observation_submission_ids:
        for observation in db.scalars(
            select(SourceObservation).where(
                SourceObservation.submission_id.in_(observation_submission_ids)
            )
        ).all():
            try:
                metadata = json.loads(observation.metadata_json or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            feed = metadata.get("feed")
            if isinstance(feed, str) and feed.strip():
                feed_by_submission_id[observation.submission_id] = feed.strip()

    def _policy_url(submission) -> str | None:
        return feed_by_submission_id.get(submission.id)

    def _passes_base_gates(
        record,
        *,
        keep_codes: frozenset[str],
        skip_non_product: bool = False,
    ) -> bool:
        link, submission, analysis, _source_record = record
        if link.relationship_type not in SUPPORTING_RELATIONSHIPS:
            return False
        idea_context = analysis.get("filter_reason_code") == "N1_IDEA_CONTEXT"
        genuine_ok = analysis.get("is_genuine_problem") is True or (
            skip_non_product and idea_context
        )
        return (
            genuine_ok
            and isinstance(analysis.get("signal_type"), str)
            and analysis.get("signal_type") in ACTIONABLE_SIGNAL_TYPES
            and analysis.get("filter_reason_code") in keep_codes
            and (
                skip_non_product
                or not is_non_product_problem(
                    cluster.title,
                    analysis.get("problem_statement"),
                    submission.text,
                )
            )
        )

    _capability_keep_codes = frozenset({
        "KEEP_DEMAND",
        "KEEP_CAPABILITY",
        "SUPPORTING_ONLY",
    })

    forum_demand_submission_ids: set[int] = set()
    forum_submission_ids = [
        submission.id
        for _, submission, _, _ in unique_records
        if submission.source_name.casefold().startswith("google developer forums")
    ]
    if forum_submission_ids:
        from app.commercial_evidence import revalidate_stored_claims

        for observation in db.scalars(
            select(SourceObservation).where(
                SourceObservation.submission_id.in_(forum_submission_ids)
            )
        ).all():
            submission = next(
                (
                    item
                    for _, item, _, _ in unique_records
                    if item.id == observation.submission_id
                ),
                None,
            )
            if submission is None:
                continue
            claims, _diagnostics = revalidate_stored_claims(
                observation,
                submission,
            )
            if any(claim.get("role") == "demand" for claim in claims):
                forum_demand_submission_ids.add(submission.id)

    def _demand_eligible_source(record) -> bool:
        submission = record[1]
        if is_market_lead_role(submission.source_name, _policy_url(submission)):
            return True
        if submission.source_name.casefold().startswith("google developer forums"):
            return (
                is_demand_role(submission.source_name)
                and submission.id in forum_demand_submission_ids
            )
        return is_demand_role(submission.source_name)

    def _keep_codes_for(record) -> frozenset[str]:
        submission = record[1]
        if is_market_lead_role(submission.source_name, _policy_url(submission)):
            return KEEP_CODES_FOR_MARKET_LEAD
        return KEEP_CODES_FOR_DEMAND

    # Demand-floor candidates: demand-role sources, plus market/idea leads.
    eligible_records = [
        record
        for record in unique_records
        if _passes_base_gates(
            record,
            keep_codes=_keep_codes_for(record),
            skip_non_product=is_market_lead_role(
                record[1].source_name,
                _policy_url(record[1]),
            ),
        )
        and _demand_eligible_source(record)
    ]
    # Capability / context corroboration (never inflate demand counts).
    supporting_capability_records = [
        record
        for record in unique_records
        if _passes_base_gates(record, keep_codes=_capability_keep_codes)
        and is_capability_or_context_role(record[1].source_name)
    ]
    # KEEP_DEMAND labels on non-demand / unknown roles — visible diagnostics only.
    role_blocked_keep_demand = [
        {
            **role_diagnostics(record[1].source_name),
            "submission_id": record[1].id,
            "filter_reason_code": record[2].get("filter_reason_code"),
            "note": (
                "Model KEEP_DEMAND ignored for demand floor because source "
                f"role is {source_role(record[1].source_name)!r}."
            ),
        }
        for record in unique_records
        if _passes_base_gates(record, keep_codes=_keep_codes_for(record))
        and not _demand_eligible_source(record)
    ]
    for link, submission, _analysis, source_record in eligible_records:
        if source_record is not None:
            eligible_distinct_source_records.add(source_record)

    willingness_signals = []
    evidence_quotes = []
    unsupported_commercial_signal = False
    for _, submission, item, _ in eligible_records:
        quotes = item.get("evidence_quotes")
        traceable_quotes = []
        if isinstance(quotes, list):
            traceable_quotes = [
                quote
                for quote in quotes
                if (
                    isinstance(quote, str)
                    and quote.strip()
                    and quote in (submission.text or "")
                )
            ]
            for quote in traceable_quotes:
                if quote not in evidence_quotes:
                    evidence_quotes.append(quote)
                if len(evidence_quotes) == 3:
                    break

        signal = item.get("willingness_to_pay_signal")
        if (
            isinstance(signal, str)
            and signal in {"explicit", "suggested"}
            and not traceable_quotes
        ):
            signal = "unclear"
            unsupported_commercial_signal = True
        willingness_signals.append(signal)

    if not eligible_records:
        commercial_signal = "unknown"
    elif "explicit" in willingness_signals:
        commercial_signal = "explicit"
    elif "suggested" in willingness_signals:
        commercial_signal = "suggested"
    elif any(
        not isinstance(signal, str)
        or signal not in {"none", "unclear"}
        for signal in willingness_signals
    ) or "unclear" in willingness_signals:
        commercial_signal = "unclear"
    elif "none" not in willingness_signals:
        commercial_signal = "unclear"
    else:
        commercial_signal = "none"

    eligible_submission_ids = [
        submission.id for _, submission, _, _ in eligible_records
    ]
    provenance_by_submission_id = (
        {
            record.submission_id: record
            for record in db.scalars(
                select(SourceRecord).where(
                    SourceRecord.submission_id.in_(eligible_submission_ids)
                )
            ).all()
        }
        if eligible_submission_ids
        else {}
    )
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    source_timestamped_records_7d = 0
    for _, submission, _, _ in eligible_records:
        source_record = provenance_by_submission_id.get(submission.id)
        source_created_at = submission.created_at
        if source_created_at.tzinfo is None:
            source_created_at = source_created_at.replace(tzinfo=timezone.utc)
        if (
            source_record is not None
            and source_record.timestamp_origin == "source"
            and source_created_at >= recent_cutoff
        ):
            source_timestamped_records_7d += 1

    buildability_values = [
        record[2].get("software_buildability")
        for record in eligible_records
    ]
    if buildability_values and all(
        value == "likely" for value in buildability_values
    ):
        software_buildability = "likely"
    elif (
        buildability_values
        and all(value == "unlikely" for value in buildability_values)
    ):
        software_buildability = "unlikely"
    else:
        software_buildability = "unclear"
    software_buildability_reasons = list(dict.fromkeys(
        reason.strip()
        for _, _, analysis, _ in eligible_records
        if isinstance(
            reason := analysis.get("software_buildability_reason"),
            str,
        )
        and reason.strip()
    ))[:3]

    eligible_analyses = [record[2] for record in eligible_records]
    eligible_submissions = [record[1] for record in eligible_records]
    eligible_platforms = {
        submission.source_name
        for _link, submission, _analysis, _ in eligible_records
    }
    section_votes: dict[str, int] = {}
    for analysis in eligible_analyses:
        section = analysis.get("section")
        if isinstance(section, str) and section.strip():
            section_votes[section] = section_votes.get(section, 0) + 1
    primary_section = (
        max(section_votes, key=section_votes.get)
        if section_votes
        else cluster.category
    )

    now = datetime.now(timezone.utc)
    records_last_30d = count_records_in_window(
        eligible_submissions, end=now, days=30
    )
    records_prior_30d = count_records_in_window(
        eligible_submissions,
        end=now - timedelta(days=30),
        days=30,
    )

    tatvic = score_tatvic_opportunity(
        eligible_analyses=eligible_analyses,
        submissions=eligible_submissions,
        source_platforms=eligible_platforms,
        item_count=len(eligible_records),
        distinct_authors=count_distinct_authors(eligible_submissions),
        records_last_30d=records_last_30d,
        records_prior_30d=records_prior_30d,
        primary_section=primary_section,
        cluster_title=cluster.title,
    )
    display_score = tatvic.score
    if software_buildability == "unlikely":
        display_score = None
    elif primary_section == "OUT_OF_SCOPE":
        display_score = None

    commercial_signal_limitation = (
        "This model-reported signal occurs in at least one eligible record, "
        "not necessarily all records. Review the linked source and quote to "
        "confirm actual customer purchase intent."
        if commercial_signal in {"explicit", "suggested"}
        else "No eligible record establishes purchase intent."
    )

    from app.commercial_qualification import build_commercial_qualification
    from app.commercial_scoring import score_commercial_opportunity_v02

    commercial_claims = []
    commercial_claim_diagnostics = {
        "proposed": 0,
        "accepted": 0,
        "rejected": [],
        "duplicate_claims": 0,
    }
    evidence_submission_ids = [
        submission.id for _, submission, _, _ in unique_records
    ]
    unique_submission_by_id = {
        submission.id: submission
        for _, submission, _, _ in unique_records
    }
    if evidence_submission_ids:
        from app.commercial_evidence import revalidate_stored_claims

        for observation in db.scalars(
            select(SourceObservation).where(
                SourceObservation.submission_id.in_(evidence_submission_ids)
            )
        ).all():
            try:
                metadata = json.loads(observation.metadata_json or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            submission = unique_submission_by_id.get(observation.submission_id)
            if submission is None:
                continue
            accepted, diagnostics = revalidate_stored_claims(
                observation,
                submission,
            )
            commercial_claims.extend(accepted)
            commercial_claim_diagnostics["proposed"] += diagnostics["proposed"]
            commercial_claim_diagnostics["accepted"] += diagnostics["accepted"]
            commercial_claim_diagnostics["duplicate_claims"] += diagnostics[
                "duplicate_claims"
            ]
            commercial_claim_diagnostics["rejected"].extend(
                {
                    **item,
                    "submission_id": submission.id,
                    "source_url": observation.source_url,
                }
                for item in diagnostics["rejected"]
            )

    supporting_capability_count = len(supporting_capability_records)
    demand_metrics = {
        **tatvic.demand_metrics,
        "demand_role_item_count": len(eligible_records),
        "supporting_capability_item_count": supporting_capability_count,
        "role_blocked_keep_demand_count": len(role_blocked_keep_demand),
    }
    commercial_qualification = build_commercial_qualification(
        cluster_title=cluster.title,
        section=tatvic.section,
        offering_angle=tatvic.offering_angle,
        smallest_first_version=tatvic.smallest_first_version,
        first_client_type=tatvic.first_client_type,
        failure_risk_line=tatvic.failure_risk_line,
        demand_analyses=eligible_analyses,
        demand_submissions=eligible_submissions,
        india_evidence_claims=[
            claim
            for claim in commercial_claims
            if claim.get("role") == "india_evidence"
        ],
        commercial_claims=commercial_claims,
        commercial_claim_diagnostics=commercial_claim_diagnostics,
        evidence_submissions=[
            submission
            for _, submission, _, _ in unique_records
        ],
        supporting_capability_count=supporting_capability_count,
        product_ready=tatvic.product_ready,
    )
    # v0.2 is computed for inspection only — never replaces active v0.1 score.
    commercial_v02 = score_commercial_opportunity_v02(
        demand_analyses=eligible_analyses,
        demand_submissions=eligible_submissions,
        supporting_capability_count=supporting_capability_count,
        product_ready=tatvic.product_ready,
        offering_angle=tatvic.offering_angle,
        qualification=commercial_qualification,
    )

    def report_evidence(records):
        record_ids = [record[1].id for record in records]
        observation_by_submission = {}
        if record_ids:
            observation_by_submission = {
                observation.submission_id: observation
                for observation in db.scalars(
                    select(SourceObservation).where(
                        SourceObservation.submission_id.in_(record_ids)
                    )
                ).all()
            }
        rows = []
        for _link, submission, analysis, _source_record in records:
            quotes = analysis.get("evidence_quotes")
            if not isinstance(quotes, list):
                continue
            observation = observation_by_submission.get(submission.id)
            valid_quotes = [
                quote
                for quote in quotes
                if isinstance(quote, str)
                and quote.strip()
                and quote in (submission.text or "")
            ][:2]
            for quote in valid_quotes:
                rows.append({
                    "submission_id": submission.id,
                    "source_name": submission.source_name,
                    "source_url": (
                        observation.canonical_url
                        if observation
                        else submission.source_url
                    ),
                    "source_date": (
                        observation.source_created_at.isoformat()
                        if observation and observation.source_created_at
                        else submission.created_at.isoformat()
                        if submission.created_at
                        else None
                    ),
                    "evidence_quote": quote,
                    "classification_reason": (
                        analysis.get("filter_reason_sentence")
                        or analysis.get("filter_reason_code")
                        or "Validated by source role and existing problem analysis."
                    ),
                })
        return rows

    demand_evidence = report_evidence(eligible_records)
    capability_context_evidence = report_evidence(
        supporting_capability_records
    )

    return {
        "cluster_id": cluster.id,
        "title": cluster.title,
        "category": cluster.category,
        "status": cluster.status,
        "created_at": cluster.created_at,
        "updated_at": cluster.updated_at,
        "score": display_score,
        "evidence_level": tatvic.evidence_level,
        "weight_version": tatvic.weight_version,
        "section": tatvic.section,
        "score_components": tatvic.components,
        "score_component_contributions": tatvic.component_contributions,
        "top10_eligible": tatvic.top10_eligible,
        "product_ready": tatvic.product_ready,
        "single_source_capped": tatvic.single_source_capped,
        "offering_angle": tatvic.offering_angle,
        "smallest_first_version": tatvic.smallest_first_version,
        "first_client_type": tatvic.first_client_type,
        "why_now": next(
            (
                value.strip()
                for _, _, analysis, _ in eligible_records
                if isinstance((value := analysis.get("why_now")), str)
                and value.strip()
            ),
            None,
        ),
        "first_customer_reach_strategy": next(
            (
                value.strip()
                for _, _, analysis, _ in eligible_records
                if isinstance(
                    (value := analysis.get("first_customer_reach_strategy")),
                    str,
                )
                and value.strip()
            ),
            None,
        ),
        "delivery_type": next(
            (
                value.strip().casefold()
                for _, _, analysis, _ in eligible_records
                if isinstance((value := analysis.get("delivery_type")), str)
                and value.strip().casefold() in {"service", "product", "unclear"}
            ),
            "unclear",
        ),
        "failure_risk_line": tatvic.failure_risk_line,
        "demand_metrics": demand_metrics,
        "supporting_capability_evidence_count": supporting_capability_count,
        "demand_evidence": demand_evidence,
        "capability_context_evidence": capability_context_evidence,
        "role_blocked_keep_demand": role_blocked_keep_demand,
        "commercial_qualification": commercial_qualification,
        "commercial_score_v02": commercial_v02,
        "software_buildability": software_buildability,
        "software_buildability_reasons": software_buildability_reasons,
        "eligibility": (
            "eligible"
            if display_score is not None
            else "review_required"
        ),
        "source_timestamped_records_7d": source_timestamped_records_7d,
        "raw_attached_record_count": raw_attached_record_count,
        "analyzed_submission_count": analyzed_record_count,
        "unique_evidence_count": len(unique_records),
        "eligible_evidence_count": len(eligible_records),
        "duplicate_analyzed_records_excluded": (
            duplicate_analyzed_records_excluded
        ),
        "duplicate_exclusions": duplicate_exclusions,
        "distinct_source_records": len(distinct_source_records),
        "eligible_distinct_source_record_count": len(
            eligible_distinct_source_records
        ),
        "unique_source_platform_count": len(source_platforms),
        "unique_demand_platform_count": len(eligible_platforms),
        "recorded_independent_records": len(recorded_independent_records),
        "verified_independent_records": 0,
        "verified_independent_source_count": 0,
        "commercial_signal": commercial_signal,
        "commercial_signal_limitation": commercial_signal_limitation,
        "reasons": tatvic.reasons,
        "evidence_provenance": evidence_provenance,
        "limitations": tatvic.limitations + [
            *(
                [
                    "A model-reported purchase-intent signal lacked a "
                    "traceable quote in its source and was treated as unclear."
                ]
                if unsupported_commercial_signal
                else []
            ),
            *(
                [
                    "One or more KEEP_DEMAND labels on non-demand sources "
                    "were ignored for the demand floor (SOURCES.md roles)."
                ]
                if role_blocked_keep_demand
                else []
            ),
            "Recorded independence flags have not been verified.",
            "No independent-source verification mechanism is implemented.",
            "Exact-text and canonical-URL duplicates are excluded from scoring.",
            "Unique source platforms do not prove independent customers or demand.",
            "Distinct source records and platforms are not verified customers.",
            "Commercial intent is model-reported and requires human review "
            "against its source excerpt.",
            "Multiple submissions may still describe the same underlying person or event.",
            "This heuristic is for investigation prioritization, not market validation.",
            "Active ranking uses weight_version v0.1; commercial_score_v02 is "
            "informational until owners activate v0.2-commercial.",
        ],
    }


@app.get(
    "/api/clusters/{cluster_id}/opportunity",
    response_model=OpportunitySummaryRead,
)
def get_cluster_opportunity(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    return build_opportunity_summary(cluster_id, db)


@app.post(
    "/api/clusters/{cluster_id}/evidence",
    response_model=EvidenceRead,
    status_code=201,
)
def attach_evidence(
    cluster_id: int,
    payload: EvidenceAttach,
    db: Session = Depends(get_db),
):
    cluster = db.get(ProblemCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Problem cluster not found.")

    submission = db.get(Submission, payload.submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found.")

    existing = db.scalar(
        select(ClusterEvidence).where(
            ClusterEvidence.cluster_id == cluster_id,
            ClusterEvidence.submission_id == payload.submission_id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="This submission is already linked to the cluster.",
        )

    evidence = ClusterEvidence(
        cluster_id=cluster_id,
        submission_id=payload.submission_id,
        is_independent=payload.is_independent,
        relationship_type=payload.relationship_type,
        match_confidence=payload.match_confidence,
        notes=payload.notes,
    )
    db.add(evidence)

    cluster.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(evidence)
    return evidence



@app.post("/api/submissions/{submission_id}/cluster")
def cluster_one_submission(
    submission_id: int,
    retry_needs_review: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    submission = db.get(Submission, submission_id)

    if submission is None:
        raise HTTPException(
            status_code=404,
            detail="Submission not found.",
        )

    if submission.analysis_status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Analyze the submission before clustering it.",
        )

    try:
        return cluster_submission(
            db,
            submission,
            retry_needs_review=retry_needs_review,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        logger.warning(
            "Semantic matching failed for submission_id=%s (%s).",
            submission_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="Semantic matching failed; no lexical fallback was used.",
        ) from exc


@app.get("/api/review-queue", response_model=ReviewQueueRead)
def list_review_queue(
    queue: Literal["all", "analysis", "clustering"] = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = []
    if queue in {"all", "analysis"}:
        submissions = db.scalars(
            select(Submission)
            .where(Submission.analysis_status.in_(("pending", "failed")))
            .order_by(Submission.created_at.asc(), Submission.id.asc())
        ).all()
        for submission in submissions:
            error_code = None
            if submission.analysis_status == "failed":
                error_code = "analysis_failed"
            elif submission.analysis_error:
                previous_error = submission.analysis_error.casefold()
                if any(word in previous_error for word in ("rate", "quota", "429")):
                    error_code = "rate_limited"
                elif "config" in previous_error:
                    error_code = "provider_not_configured"
                else:
                    error_code = "analysis_pending"
            items.append({
                "submission_id": submission.id,
                "queue": "analysis",
                "status": submission.analysis_status,
                "analysis_status": submission.analysis_status,
                "clustering_status": None,
                "error_code": error_code,
                "source_name": submission.source_name,
                "source_url": submission.source_url,
                "created_at": submission.created_at,
                "text_preview": " ".join(submission.text.split())[:240],
                "cluster_id": None,
                "action": (
                    "retry_analysis"
                    if submission.analysis_status == "failed"
                    else "run_analysis"
                ),
            })

    if queue in {"all", "clustering"}:
        state_rows = db.execute(
            select(PipelineSubmissionState, Submission)
            .join(
                Submission,
                Submission.id == PipelineSubmissionState.submission_id,
            )
            .where(
                PipelineSubmissionState.clustering_status.in_(
                    ("needs_review", "failed")
                )
            )
            .order_by(Submission.created_at.asc(), Submission.id.asc())
        ).all()
        for state, submission in state_rows:
            needs_review = state.clustering_status == "needs_review"
            items.append({
                "submission_id": submission.id,
                "queue": "clustering",
                "status": state.clustering_status,
                "analysis_status": submission.analysis_status,
                "clustering_status": state.clustering_status,
                "error_code": (
                    "clustering_needs_review"
                    if needs_review
                    else (
                        "embedding_provider_failed"
                        if state.message == "embedding_error"
                        else "clustering_failed"
                    )
                ),
                "source_name": submission.source_name,
                "source_url": submission.source_url,
                "created_at": submission.created_at,
                "text_preview": " ".join(submission.text.split())[:240],
                "cluster_id": state.cluster_id,
                "action": (
                    "inspect_match_or_explicitly_retry"
                    if needs_review
                    else "retry_clustering"
                ),
            })

    items.sort(
        key=lambda item: (
            item["created_at"].replace(tzinfo=timezone.utc)
            if item["created_at"].tzinfo is None
            else item["created_at"],
            item["submission_id"],
            item["queue"],
        )
    )
    return {
        "items": items[offset:offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "queue": queue,
    }


@app.post("/api/submissions/analyze-pending")
def analyze_pending_submissions(
    limit: int = Query(default=10, ge=1, le=30),
    retry_failed: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    results = analyze_pending_batch(
        db,
        limit,
        analyzer=analyze_submission,
        retry_failed=retry_failed,
    )
    if results.get("provider_not_configured"):
        detail = results.pop("configuration_error")
        results.pop("provider_not_configured")
        raise HTTPException(status_code=503, detail=detail)
    results.pop("provider_not_configured", None)
    results.pop("configuration_error", None)
    return results


@app.get("/api/sources/yield")
def source_yield_endpoint(db: Session = Depends(get_db)):
    """Report measured ingestion yield by source/community."""
    return {"communities": community_yield(db)}


@app.post("/api/pipeline/run")
def run_pipeline_endpoint(
    payload: PipelineRunRequest,
    db: Session = Depends(get_db),
):
    return run_pipeline(
        payload,
        db,
        analyzer=analyze_submission,
        summarize_opportunity=build_opportunity_summary,
        retry_interrupted_run=payload.recover_interrupted_run,
    )


@app.get("/api/pipeline/status", response_model=PipelineStatusRead)
def pipeline_status_endpoint(db: Session = Depends(get_db)):
    return get_pipeline_status(db)


def _pipeline_run_summary(run: PipelineRun) -> dict:
    try:
        requested_sources = json.loads(run.requested_sources)
        stage_counts = json.loads(run.stage_counts)
        failures = json.loads(run.failures)
    except (TypeError, json.JSONDecodeError):
        requested_sources, stage_counts, failures = [], {}, []
    return {
        "run_id": run.id,
        "status": run.status,
        "requested_sources": (
            requested_sources if isinstance(requested_sources, list) else []
        ),
        "stage_counts": stage_counts if isinstance(stage_counts, dict) else {},
        "failures": failures if isinstance(failures, list) else [],
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


@app.get("/api/pipeline/runs", response_model=PipelineRunListRead)
def list_pipeline_runs(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count()).select_from(PipelineRun)) or 0
    runs = db.scalars(
        select(PipelineRun)
        .order_by(desc(PipelineRun.started_at), desc(PipelineRun.id))
        .offset(offset)
        .limit(limit)
    ).all()
    items = []
    for run in runs:
        payload = _pipeline_run_summary(run)
        payload["collected_submission_ids"] = db.scalars(
            select(PipelineRunSubmission.submission_id)
            .where(PipelineRunSubmission.run_id == run.id)
            .order_by(PipelineRunSubmission.submission_id.asc())
        ).all()
        items.append(payload)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/pipeline/runs/{run_id}", response_model=PipelineRunRead)
def get_pipeline_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found.")

    links = db.scalars(
        select(PipelineRunSubmission)
        .where(PipelineRunSubmission.run_id == run_id)
        .order_by(PipelineRunSubmission.submission_id.asc())
    ).all()
    submission_ids = [link.submission_id for link in links]
    submissions = (
        db.scalars(
            select(Submission).where(Submission.id.in_(submission_ids))
        ).all()
        if submission_ids
        else []
    )
    submissions_by_id = {item.id: item for item in submissions}
    states = (
        {
            state.submission_id: state
            for state in db.scalars(
                select(PipelineSubmissionState).where(
                    PipelineSubmissionState.submission_id.in_(submission_ids)
                )
            ).all()
        }
        if submission_ids
        else {}
    )

    run_submissions = []
    for link in links:
        submission = submissions_by_id.get(link.submission_id)
        if submission is None:
            continue
        state = states.get(link.submission_id)
        try:
            metadata = json.loads(link.source_metadata or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        run_submissions.append({
            "submission_id": submission.id,
            "source_name": submission.source_name,
            "source_url": submission.source_url,
            "analysis_status": submission.analysis_status,
            "clustering_status": (
                state.clustering_status if state else "not_attempted"
            ),
            "cluster_id": state.cluster_id if state else None,
            "source_metadata": metadata if isinstance(metadata, dict) else {},
        })

    review_candidate_cluster_ids = sorted({
        state.cluster_id
        for state in states.values()
        if state.clustering_status == "needs_review"
        and state.cluster_id is not None
    })

    evidence_links = (
        db.scalars(
            select(ClusterEvidence)
            .where(ClusterEvidence.submission_id.in_(submission_ids))
            .order_by(ClusterEvidence.cluster_id.asc())
        ).all()
        if submission_ids
        else []
    )
    run_evidence_by_cluster: dict[int, list[int]] = {}
    for evidence in evidence_links:
        run_evidence_by_cluster.setdefault(evidence.cluster_id, []).append(
            evidence.submission_id
        )

    opportunity_clusters = []
    for cluster_id, run_evidence_ids in sorted(
        run_evidence_by_cluster.items()
    ):
        cluster = db.get(ProblemCluster, cluster_id)
        if cluster is None:
            continue
        total_attached = db.scalar(
            select(func.count())
            .select_from(ClusterEvidence)
            .where(ClusterEvidence.cluster_id == cluster_id)
        ) or 0
        opportunity_clusters.append({
            "cluster_id": cluster_id,
            "title": cluster.title,
            "run_submission_ids": sorted(set(run_evidence_ids)),
            "run_evidence_count": len(set(run_evidence_ids)),
            "total_attached_evidence_count": total_attached,
            "contains_evidence_outside_run": (
                total_attached > len(set(run_evidence_ids))
            ),
            "opportunity_summary": build_opportunity_summary(
                cluster_id,
                db,
            ),
        })

    return {
        **_pipeline_run_summary(run),
        "collected_submission_ids": submission_ids,
        "submissions": run_submissions,
        "opportunity_clusters": opportunity_clusters,
        "review_candidate_cluster_ids": review_candidate_cluster_ids,
    }
