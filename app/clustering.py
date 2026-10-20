
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from unicodedata import normalize as unicode_normalize
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from google import genai
from google.genai import errors as genai_errors
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ClusterEvidence,
    PipelineSubmissionState,
    ProblemCluster,
    Submission,
)


EMBEDDING_MODEL = "gemini-embedding-001"

# Initial operational thresholds, NOT calibrated probabilities.
# Similarity values must be evaluated against labelled examples before
# they can be treated as trustworthy matching thresholds.
AUTO_MATCH_THRESHOLD = 0.78
REVIEW_THRESHOLD = 0.60

_GENERIC_RELEVANCE_TERMS = frozenset({
    "a",
    "about",
    "an",
    "and",
    "app",
    "apps",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "challenge",
    "company",
    "customer",
    "customers",
    "difficulty",
    "do",
    "for",
    "from",
    "get",
    "help",
    "how",
    "i",
    "in",
    "is",
    "it",
    "make",
    "my",
    "need",
    "of",
    "on",
    "or",
    "our",
    "people",
    "problem",
    "product",
    "solution",
    "struggle",
    "system",
    "the",
    "their",
    "them",
    "there",
    "they",
    "this",
    "to",
    "tool",
    "use",
    "user",
    "users",
    "using",
    "want",
    "we",
    "when",
    "with",
    "work",
})


def normalize_text(text: str | None) -> str:
    """Normalize text for exact duplicate detection, not semantic matching."""
    normalized = unicode_normalize("NFKC", text or "").casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _relevance_terms(text: str | None) -> set[str]:
    return {
        term
        for term in normalize_text(text).split()
        if len(term) > 2 and term not in _GENERIC_RELEVANCE_TERMS
    }


def _is_relevant_candidate(details: dict, cluster: ProblemCluster) -> bool:
    """Require problem-topic overlap before cosine can nominate a cluster."""
    submission_problem_terms = _relevance_terms(
        f"{details['title']} {details['problem_statement']}"
    )
    cluster_problem_terms = _relevance_terms(
        f"{cluster.title} {cluster.problem_statement}"
    )
    shared_problem_terms = submission_problem_terms & cluster_problem_terms
    if len(shared_problem_terms) >= 2:
        return True

    if not shared_problem_terms:
        return False

    submission_context = (
        details["affected_user"],
        details["category"],
        details["industry"],
    )
    cluster_context = (
        cluster.affected_user,
        cluster.category,
        cluster.industry,
    )
    shared_context_terms = set().union(
        *(
            _relevance_terms(submission_value)
            & _relevance_terms(cluster_value)
            for submission_value, cluster_value in zip(
                submission_context,
                cluster_context,
            )
            if submission_value and cluster_value
        )
    )
    return len(shared_context_terms) >= 2


def text_fingerprint(text: str | None) -> str:
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonicalize_url(url: str | None) -> str | None:
    """Remove common tracking parameters while preserving meaningful URL data."""
    if not isinstance(url, str) or not url.strip():
        return None

    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        if any(char.isspace() for char in parts.hostname):
            return None

        tracking_prefixes = ("utm_",)
        tracking_names = {
            "fbclid", "gclid", "dclid", "ref_src",
            "igshid", "mc_cid", "mc_eid",
        }
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in tracking_names
            and not key.casefold().startswith(tracking_prefixes)
        ]

        host = parts.hostname.casefold()
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        if port and not (
            parts.scheme.lower() == "https" and parts.port == 443
        ) and not (
            parts.scheme.lower() == "http" and parts.port == 80
        ):
            host = f"{host}:{port}"

        user_info, separator, _ = parts.netloc.rpartition("@")
        if separator:
            host = f"{user_info}@{host}"

        return urlunsplit((
            parts.scheme.lower(),
            host,
            parts.path,
            urlencode(query),
            "",  # URL fragments are not source identity.
        ))
    except (ValueError, TypeError):
        return None


def _validate_embedding_vectors(
    vectors: list[list[float]],
    expected_count: int,
) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise RuntimeError("Gemini returned an invalid number of embeddings.")

    validated: list[list[float]] = []
    expected_dimensions: int | None = None
    for vector in vectors:
        if not isinstance(vector, list) or not vector:
            raise RuntimeError("Gemini returned an empty or invalid embedding.")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            raise RuntimeError(
                "Gemini returned non-finite or non-numeric embedding values."
            )

        values = [float(value) for value in vector]
        if not any(value != 0 for value in values):
            raise RuntimeError("Gemini returned a zero-length embedding.")

        if expected_dimensions is None:
            expected_dimensions = len(values)
        elif len(values) != expected_dimensions:
            raise RuntimeError(
                "Gemini returned embeddings with inconsistent dimensions."
            )
        validated.append(values)

    return validated



def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Return semantic embeddings using the project's existing Gemini settings."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Check .env and restart the API."
        )

    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
        )
    except genai_errors.APIError as exc:
        raise RuntimeError(
            f"Gemini embedding request failed (HTTP {exc.code})."
        ) from exc

    embeddings = getattr(response, "embeddings", None)
    if not embeddings or len(embeddings) != len(texts):
        raise RuntimeError(
            "Gemini returned an incomplete embedding response."
        )

    vectors: list[list[float]] = []
    for item in embeddings:
        values = getattr(item, "values", None)
        if not values:
            raise RuntimeError("Gemini returned an empty embedding.")
        try:
            vectors.append(list(values))
        except TypeError as exc:
            raise RuntimeError(
                "Gemini returned an invalid embedding."
            ) from exc

    return _validate_embedding_vectors(vectors, len(texts))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        raise ValueError("Embedding vectors must have matching dimensions.")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in (*left, *right)
    ):
        raise ValueError("Embedding vectors must contain finite numbers.")

    left_norm = math.hypot(*left)
    right_norm = math.hypot(*right)

    if left_norm == 0 or right_norm == 0:
        raise ValueError("Embedding vectors must not be zero vectors.")

    similarity = sum(
        (a / left_norm) * (b / right_norm)
        for a, b in zip(left, right)
    )
    if not math.isfinite(similarity):
        raise ValueError("Embedding similarity must be finite.")
    return max(-1.0, min(1.0, similarity))


def parse_analysis(submission: Submission) -> dict:
    if submission.analysis_status != "completed":
        raise ValueError("Analyze the submission before clustering it.")

    if not submission.analysis_json:
        raise ValueError("The submission has no saved analysis.")

    try:
        data = json.loads(submission.analysis_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Saved analysis is not valid JSON.") from exc

    if not isinstance(data, dict):
        raise ValueError("Saved analysis must be a JSON object.")

    def first_text(*names: str) -> str:
        for name in names:
            value = data.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    problem = first_text(
        "problem_statement",
        "problem_summary",
        "problem",
        "summary",
        "normalized_problem",
    )
    title = first_text("title", "problem_title", "short_title", "headline")

    if not problem:
        # Keep the raw complaint as a fallback, but do not pretend the
        # model produced a normalized problem statement.
        problem = submission.text.strip()

    if not title:
        title = problem[:100].strip()

    return {
        "title": title,
        "problem_statement": problem,
        "affected_user": first_text(
            "affected_user", "target_user", "user_segment"
        ) or None,
        "category": first_text("category", "problem_category") or None,
        "industry": first_text("industry", "sector") or None,
    }


def source_duplicates(
    db: Session,
    submission: Submission,
) -> list[Submission]:
    """Detect repeated evidence by exact text or canonical source URL.

    Semantic similarity is deliberately NOT used to infer duplicates:
    different people can independently experience the same problem.
    """
    normalized_text = normalize_text(submission.text)
    url = canonicalize_url(submission.source_url)

    others = db.scalars(
        select(Submission)
        .where(Submission.id != submission.id)
        .order_by(Submission.id.asc())
    ).all()

    return [
        other
        for other in others
        if (
            normalized_text
            and normalize_text(other.text) == normalized_text
        )
        or (
            url
            and (other_url := canonicalize_url(other.source_url))
            and url == other_url
        )
    ]


def source_duplicate(
    db: Session,
    submission: Submission,
) -> Submission | None:
    """Return the earliest exact duplicate by stable submission ID."""
    duplicates = source_duplicates(db, submission)
    return duplicates[0] if duplicates else None


def _persist_clustering_state(
    db: Session,
    submission_id: int,
    status: str,
    *,
    cluster_id: int | None = None,
    message: str | None = None,
) -> None:
    state = db.get(PipelineSubmissionState, submission_id)
    if state is None:
        state = PipelineSubmissionState(
            submission_id=submission_id,
            clustering_status=status,
        )
        db.add(state)
    state.clustering_status = status
    state.cluster_id = cluster_id
    state.message = message
    db.commit()


def cluster_submission(
    db: Session,
    submission: Submission,
    *,
    retry_needs_review: bool = False,
) -> dict:
    pipeline_state = db.get(PipelineSubmissionState, submission.id)
    if (
        pipeline_state is not None
        and pipeline_state.clustering_status == "needs_review"
        and not retry_needs_review
    ):
        stored_similarity = None
        if pipeline_state.message and pipeline_state.message.startswith(
            "needs_review:"
        ):
            try:
                stored_similarity = float(
                    pipeline_state.message.partition(":")[2]
                )
            except ValueError:
                stored_similarity = None
        review_cluster = (
            db.get(ProblemCluster, pipeline_state.cluster_id)
            if pipeline_state.cluster_id is not None
            else None
        )
        return {
            "decision": "needs_review",
            "cluster_id": pipeline_state.cluster_id,
            "title": review_cluster.title if review_cluster else None,
            "similarity": stored_similarity,
            "score_type": "cosine_similarity_not_probability",
            "message": "Possible semantic match; human review required.",
        }

    if (
        pipeline_state is not None
        and pipeline_state.clustering_status == "needs_review"
        and retry_needs_review
    ):
        pipeline_state.clustering_status = "not_attempted"
        pipeline_state.cluster_id = None
        pipeline_state.message = None
        db.commit()

    existing_link = db.scalar(
        select(ClusterEvidence)
        .where(ClusterEvidence.submission_id == submission.id)
        .order_by(ClusterEvidence.id.asc())
    )

    if existing_link is not None:
        cluster = db.get(ProblemCluster, existing_link.cluster_id)
        _persist_clustering_state(
            db,
            submission.id,
            "clustered",
            cluster_id=existing_link.cluster_id,
            message="already_clustered",
        )
        return {
            "decision": "already_clustered",
            "cluster_id": existing_link.cluster_id,
            "title": cluster.title if cluster else None,
            "similarity": existing_link.match_confidence,
        }

    details = parse_analysis(submission)

    # Stop exact duplicates and repeated URLs from inflating evidence counts.
    matching_duplicates = source_duplicates(db, submission)
    duplicate = matching_duplicates[0] if matching_duplicates else None
    if duplicate is not None:
        duplicate_ids = [item.id for item in matching_duplicates]
        linked_duplicate = db.scalars(
            select(ClusterEvidence)
            .where(ClusterEvidence.submission_id.in_(duplicate_ids))
            .order_by(
                ClusterEvidence.submission_id.asc(),
                ClusterEvidence.id.asc(),
            )
        ).first()

        if linked_duplicate is not None:
            _persist_clustering_state(
                db,
                submission.id,
                "duplicate",
                cluster_id=linked_duplicate.cluster_id,
                message="duplicate_evidence",
            )
            return {
                "decision": "duplicate_evidence",
                "duplicate_of_submission_id": linked_duplicate.submission_id,
                "existing_cluster_id": linked_duplicate.cluster_id,
                "message": (
                    "Duplicate detected. Existing clustered evidence "
                    "was preserved; no additional evidence was added."
                ),
            }

        # A pending earliest copy does not block an analyzed copy. If an
        # earlier analyzed copy exists, keep it as the deterministic choice.
        canonical_duplicate = next(
            (
                item for item in matching_duplicates
                if item.analysis_status == "completed"
            ),
            duplicate,
        )
        if canonical_duplicate.analysis_status == "completed":
            _persist_clustering_state(
                db,
                submission.id,
                "duplicate",
                message="duplicate_evidence",
            )
            return {
                "decision": "duplicate_evidence",
                "duplicate_of_submission_id": canonical_duplicate.id,
                "existing_cluster_id": None,
                "message": (
                    "Duplicate detected, but the original record is not "
                    "clustered yet. Cluster the analyzed record first."
                ),
            }

    clusters = db.scalars(
        select(ProblemCluster).order_by(desc(ProblemCluster.updated_at))
    ).all()

    # Source provenance alone is not enough to assert independence.
    if not clusters:
        return _create_cluster(db, submission, details)

    query_text = (
        f"Problem: {details['problem_statement']}\n"
        f"Affected user: {details['affected_user'] or 'Unknown'}\n"
        f"Category: {details['category'] or 'Unknown'}\n"
        f"Industry: {details['industry'] or 'Unknown'}"
    )

    cluster_texts = [
        (
            f"Title: {cluster.title}\n"
            f"Problem: {cluster.problem_statement}\n"
            f"Affected user: {cluster.affected_user or 'Unknown'}\n"
            f"Category: {cluster.category or 'Unknown'}\n"
            f"Industry: {cluster.industry or 'Unknown'}"
        )
        for cluster in clusters
    ]

    vectors = _validate_embedding_vectors(
        get_embeddings([query_text, *cluster_texts]),
        len(cluster_texts) + 1,
    )
    query_vector = vectors[0]

    candidates = [
        (cosine_similarity(query_vector, vector), cluster)
        for cluster, vector in zip(clusters, vectors[1:])
        if _is_relevant_candidate(details, cluster)
    ]
    if not candidates:
        return _create_cluster(db, submission, details)

    candidates.sort(key=lambda item: (-item[0], item[1].id))

    best_score, best_cluster = candidates[0]
    score = round(best_score, 4)

    if best_score >= AUTO_MATCH_THRESHOLD:
        evidence = ClusterEvidence(
            cluster_id=best_cluster.id,
            submission_id=submission.id,
            # A source URL alone does not verify independent demand.
            is_independent=False,
            relationship_type="supports",
            # This is a cosine similarity, NOT a match probability.
            match_confidence=score,
            notes=(
                f"Semantic embedding similarity={score}; "
                f"embedding_model={EMBEDDING_MODEL}. "
                + (
                    "Source URL supplied; check source-level provenance."
                    if canonicalize_url(submission.source_url)
                    else "Independence unverified: no source URL supplied."
                )
            ),
        )
        db.add(evidence)
        _persist_clustering_state(
            db,
            submission.id,
            "clustered",
            cluster_id=best_cluster.id,
            message="matched",
        )
        best_cluster.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(evidence)

        return {
            "decision": "matched",
            "cluster_id": best_cluster.id,
            "title": best_cluster.title,
            "similarity": score,
            "score_type": "cosine_similarity_not_probability",
            "evidence_id": evidence.id,
            "independence_verified": evidence.is_independent,
        }

    if best_score >= REVIEW_THRESHOLD:
        _persist_clustering_state(
            db,
            submission.id,
            "needs_review",
            cluster_id=best_cluster.id,
            message=f"needs_review:{score}",
        )
        return {
            "decision": "needs_review",
            "cluster_id": best_cluster.id,
            "title": best_cluster.title,
            "similarity": score,
            "score_type": "cosine_similarity_not_probability",
            "message": "Possible semantic match; human review required.",
        }

    return _create_cluster(db, submission, details)


def _create_cluster(
    db: Session,
    submission: Submission,
    details: dict,
) -> dict:
    cluster = ProblemCluster(
        title=details["title"],
        problem_statement=details["problem_statement"],
        affected_user=details["affected_user"],
        category=details["category"],
        industry=details["industry"],
    )
    db.add(cluster)
    db.flush()

    has_source_url = bool(canonicalize_url(submission.source_url))
    evidence = ClusterEvidence(
        cluster_id=cluster.id,
        submission_id=submission.id,
        # A source URL records provenance but does not verify independence.
        is_independent=False,
        relationship_type="supports",
        # There is no similarity comparison for a new cluster.
        match_confidence=None,
        notes=(
            "Initial evidence for a new candidate cluster. "
            + (
                "Source URL supplied; provenance still requires validation."
                if has_source_url
                else "Independence unverified: no source URL supplied."
            )
        ),
    )
    db.add(evidence)
    _persist_clustering_state(
        db,
        submission.id,
        "clustered",
        cluster_id=cluster.id,
        message="created",
    )
    db.commit()
    db.refresh(cluster)
    db.refresh(evidence)

    return {
        "decision": "created",
        "cluster_id": cluster.id,
        "title": cluster.title,
        "similarity": None,
        "evidence_id": evidence.id,
        "independence_verified": evidence.is_independent,
    }
