import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session
from app.db import Base, engine, get_db
from app.models import ClusterEvidence, ProblemCluster, Submission
from app.schemas import SubmissionCreate, SubmissionRead
from app.services.gemini import (
    PROMPT_VERSION,
    GeminiAnalysisError,
    GeminiNotConfigured,
    GeminiRateLimitError,
    analyze_submission,
)
from app.clustering import cluster_submission, canonicalize_url
from app.ingestion import router as ingestion_router, normalize


from app.social_ingestion import router as social_ingestion_router

import json
from app.ranking import score_opportunity

from app.cluster_schemas import (
    ClusterCreate,
    ClusterDetail,
    ClusterRead,
    EvidenceAttach,
    EvidenceRead,
)
from app.clustering import cluster_submission
from app.ingestion import router as ingestion_router

from datetime import datetime, timezone 


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="AI Labs Problem Discovery API",
    description="Local-first, evidence-backed problem discovery. Starter vertical slice.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(ingestion_router)
app.include_router(social_ingestion_router)




@app.get("/health")
def health():
    return {"status": "ok", "service": "ai-labs-problem-discovery", "version": app.version}


@app.post("/api/submissions", response_model=SubmissionRead, status_code=201)
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
def analyze_one_submission(submission_id: int, db: Session = Depends(get_db)):
    item = db.get(Submission, submission_id)
    if not item:
        raise HTTPException(status_code=404, detail="Submission not found.")

    item.analysis_status = "processing"
    item.analysis_error = None
    db.commit()

    try:
        result, model_name = analyze_submission(item.text, item.source_name, item.language)
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
        item.analysis_error = str(exc)
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    
    except GeminiRateLimitError as exc:
        item.analysis_status = "pending"
        item.analysis_error = str(exc)
        db.commit()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    
    except GeminiAnalysisError as exc:
        logger.exception("Analysis failed for submission_id=%s", submission_id)
        item.analysis_status = "failed"
        item.analysis_error = str(exc)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    
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
    independent_count = sum(1 for item in evidence if item.is_independent)
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
        "evidence": evidence,
    }

@app.get("/api/clusters/{cluster_id}/opportunity")
def get_cluster_opportunity(
    cluster_id: int,
    db: Session = Depends(get_db),
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
    duplicate_analyzed_records_excluded = 0
    analyses = []
    distinct_source_records = set()
    recorded_independent_records = set()
    source_platforms = set()
    seen_urls = set()
    seen_texts = set()

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
        canonical_url = (
            canonicalize_url(submission.source_url)
            if submission.source_url
            else None
        )
        
        normalized_text = normalize(submission.text)

        is_duplicate = (
            (canonical_url and canonical_url in seen_urls)
            or (normalized_text and normalized_text in seen_texts)
        )

        if canonical_url:
            seen_urls.add(canonical_url)
        if normalized_text:
            seen_texts.add(normalized_text)

        if is_duplicate:
            duplicate_analyzed_records_excluded += 1
            continue

        source_record = (
            canonical_url
            or normalized_text
            or submission.id
        )
        analyses.append(analysis)
        distinct_source_records.add(source_record)
        source_platforms.add(submission.source_name)

        if link.is_independent:
            recorded_independent_records.add(source_record)

    if not analyses:
        return {
            "cluster_id": cluster.id,
            "title": cluster.title,
            "score": None,
            "evidence_level": "insufficient_analyzed_evidence",
            "raw_attached_record_count": raw_attached_record_count,
            "analyzed_submission_count": analyzed_record_count,
            "unique_evidence_count": len(analyses),
            "duplicate_analyzed_records_excluded": (
                duplicate_analyzed_records_excluded
            ),
            "distinct_source_records": 0,
            "unique_source_platform_count": len(source_platforms),
            "recorded_independent_records": 0,
            "commercial_signal": "unknown",
            "limitations": [
                "No completed analyses are attached to this cluster.",
                "A score cannot be calculated until evidence is analyzed.",
                "Exact-text and canonical-URL duplicates are excluded from scoring.",
                "Unique source platforms do not prove independent customers or demand.",
            ],
        }

    # Aggregate conservatively: use the median of per-submission scores
    # rather than allowing one unusually high score to dominate.
    def median_score(key: str) -> int:
        values = sorted(
            max(1, min(5, value))
            for item in analyses
            if isinstance(
                value := item.get(key), int
            ) and not isinstance(value, bool)
        )
        if not values:
            return 1

        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]

        return round((values[middle - 1] + values[middle]) / 2)

    aggregate_analysis = {
        "is_genuine_problem": any(
            item.get("is_genuine_problem") is True
            for item in analyses
        ),
        "signal_type": (
            "pain_point"
            if any(
                item.get("is_genuine_problem") is True
                and item.get("signal_type")
                not in {"general_discussion", "noise"}
                for item in analyses
            )
            else "general_discussion"
        ),
        "urgency_1_to_5": median_score("urgency_1_to_5"),
        "frequency_signal_1_to_5": median_score(
            "frequency_signal_1_to_5"
        ),
        "evidence_strength_1_to_5": median_score(
            "evidence_strength_1_to_5"
        ),
        "willingness_to_pay_signal": (
            "explicit"
            if any(
                item.get("willingness_to_pay_signal") == "explicit"
                for item in analyses
            )
            else "suggested"
            if any(
                item.get("willingness_to_pay_signal") == "suggested"
                for item in analyses
            )
            else "none"
        ),
        "evidence_quotes": [
            quote
            for item in analyses
            for quote in item.get("evidence_quotes", [])
            if isinstance(quote, str) and quote.strip()
        ][:3],
    }

    result = score_opportunity(
        aggregate_analysis,
        distinct_sources=len(distinct_source_records),
        verified_independent_sources=0,
    )

    return {
        "cluster_id": cluster.id,
        "title": cluster.title,
        "score": result.score,
        "evidence_level": result.evidence_level,
        "raw_attached_record_count": raw_attached_record_count,
        "analyzed_submission_count": analyzed_record_count,
        "unique_evidence_count": len(analyses),
        "duplicate_analyzed_records_excluded": (
            duplicate_analyzed_records_excluded
        ),
        "distinct_source_records": len(distinct_source_records),
        "unique_source_platform_count": len(source_platforms),
        "recorded_independent_records": len(recorded_independent_records),
        "commercial_signal": aggregate_analysis[
            "willingness_to_pay_signal"
        ],
        "reasons": result.reasons,
        "limitations": result.limitations + [
            "Recorded independence flags have not been verified.",
            "Exact-text and canonical-URL duplicates are excluded from scoring.",
            "Unique source platforms do not prove independent customers or demand.",
            "Multiple submissions may still describe the same underlying person or event.",
            "This heuristic is for investigation prioritization, not market validation.",
        ],
    }


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
        return cluster_submission(db, submission)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        # Do not silently fall back to the old lexical matcher.
        raise HTTPException(
            status_code=502,
            detail=f"Semantic matching failed: {exc}",
        ) from exc


@app.post("/api/submissions/analyze-pending")
def analyze_pending_submissions(
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_db),
):
    pending = db.scalars(
        select(Submission)
        .where(Submission.analysis_status == "pending")
        .order_by(Submission.created_at.asc())
        .limit(limit)
    ).all()

    results = {
        "processed": 0,
        "completed": 0,
        "failed": 0,
        "rate_limited": False,
        "items": [],
    }

    for submission in pending:
        submission.analysis_status = "processing"
        submission.analysis_error = None
        db.commit()

        try:
            extraction, model_name = analyze_submission(
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

        except GeminiNotConfigured as exc:
            submission.analysis_status = "pending"
            submission.analysis_error = str(exc)
            db.commit()
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        except GeminiRateLimitError as exc:
            # Stop the batch immediately. Keep this record retryable.
            submission.analysis_status = "pending"
            submission.analysis_error = str(exc)
            results["rate_limited"] = True
            results["items"].append({
                "submission_id": submission.id,
                "status": "pending",
                "error": str(exc),
            })
            db.commit()
            results["processed"] += 1
            break

        except GeminiAnalysisError as exc:
            submission.analysis_status = "failed"
            submission.analysis_error = str(exc)
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
