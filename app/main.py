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
    score_opportunity,
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

from app.cluster_schemas import (
    ClusterCreate,
    ClusterDetail,
    ClusterRead,
    EvidenceAttach,
    EvidenceRead,
)
from app.operator_schemas import (
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
    create_weekly_snapshot,
    get_weekly_snapshot,
    list_weekly_snapshots,
    weekly_trend,
)


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
    clusters = db.scalars(
        statement.order_by(ProblemCluster.id.asc())
    ).all()

    summaries = [
        build_opportunity_summary(cluster.id, db)
        for cluster in clusters
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
    eligible_records = [
        record
        for record in unique_records
        if record[0].relationship_type in SUPPORTING_RELATIONSHIPS
        and record[2].get("is_genuine_problem") is True
        and isinstance(record[2].get("signal_type"), str)
        and record[2].get("signal_type") in ACTIONABLE_SIGNAL_TYPES
    ]
    for link, submission, _analysis, source_record in eligible_records:
        if source_record is not None:
            eligible_distinct_source_records.add(source_record)

    # A lower median keeps an even-sized tie conservative and deterministic.
    def median_score(key: str) -> int | None:
        record_values = [
            value
            for _, _, item, _ in eligible_records
            if isinstance(
                value := item.get(key), int
            )
            and not isinstance(value, bool)
            and 1 <= value <= 5
        ]
        if not record_values:
            return None
        values = sorted(
            value
            if isinstance(value := item.get(key), int)
            and not isinstance(value, bool)
            and 1 <= value <= 5
            else 1
            for _, _, item, _ in eligible_records
        )
        return values[(len(values) - 1) // 2]

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

    aggregate_analysis = {
        "is_genuine_problem": bool(eligible_records),
        "signal_type": (
            eligible_records[0][2].get("signal_type")
            if eligible_records
            else "general_discussion"
        ),
        "urgency_1_to_5": median_score("urgency_1_to_5"),
        "frequency_signal_1_to_5": median_score(
            "frequency_signal_1_to_5"
        ),
        "evidence_strength_1_to_5": median_score(
            "evidence_strength_1_to_5"
        ),
        "willingness_to_pay_signal": commercial_signal,
        "evidence_quotes": evidence_quotes,
        "software_buildability": software_buildability,
    }

    result = score_opportunity(
        aggregate_analysis,
        distinct_sources=len(eligible_distinct_source_records),
        verified_independent_sources=0,
        source_timestamped_records_7d=source_timestamped_records_7d,
    )

    commercial_signal_limitation = (
        "This model-reported signal occurs in at least one eligible record, "
        "not necessarily all records. Review the linked source and quote to "
        "confirm actual customer purchase intent."
        if commercial_signal in {"explicit", "suggested"}
        else "No eligible record establishes purchase intent."
    )

    return {
        "cluster_id": cluster.id,
        "title": cluster.title,
        "category": cluster.category,
        "status": cluster.status,
        "created_at": cluster.created_at,
        "updated_at": cluster.updated_at,
        "score": result.score,
        "evidence_level": result.evidence_level,
        "software_buildability": result.software_buildability,
        "software_buildability_reasons": software_buildability_reasons,
        "eligibility": result.eligibility,
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
        "recorded_independent_records": len(recorded_independent_records),
        "verified_independent_records": 0,
        "verified_independent_source_count": 0,
        "commercial_signal": commercial_signal,
        "commercial_signal_limitation": commercial_signal_limitation,
        "reasons": result.reasons,
        "evidence_provenance": evidence_provenance,
        "limitations": result.limitations + [
            *(
                [
                    "A model-reported purchase-intent signal lacked a "
                    "traceable quote in its source and was treated as unclear."
                ]
                if unsupported_commercial_signal
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
