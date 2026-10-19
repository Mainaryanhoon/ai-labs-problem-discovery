
import hashlib
import json
from app.config import get_settings
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from google import genai
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import ClusterEvidence, ProblemCluster, Submission


EMBEDDING_MODEL = "gemini-embedding-001"

# Initial operational thresholds, NOT calibrated probabilities.
# Similarity values must be evaluated against labelled examples before
# they can be treated as trustworthy matching thresholds.
AUTO_MATCH_THRESHOLD = 0.78
REVIEW_THRESHOLD = 0.60


def normalize_text(text: str) -> str:
    """Normalize text for exact duplicate detection, not semantic matching."""
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def text_fingerprint(text: str) -> str:
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonicalize_url(url: str | None) -> str | None:
    """Remove common tracking parameters while preserving meaningful URL data."""
    if not url or not url.strip():
        return None

    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
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
        if parts.port and not (
            parts.scheme.lower() == "https" and parts.port == 443
        ) and not (
            parts.scheme.lower() == "http" and parts.port == 80
        ):
            host = f"{host}:{parts.port}"

        path = parts.path.rstrip("/") or "/"
        return urlunsplit((
            parts.scheme.lower(),
            host,
            path,
            urlencode(sorted(query)),
            "",  # URL fragments are not source identity.
        ))
    except (ValueError, TypeError):
        return None



def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Return semantic embeddings using the project's existing Gemini settings."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Check .env and restart the API."
        )

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
    )

    embeddings = getattr(response, "embeddings", None)
    if not embeddings or len(embeddings) != len(texts):
        raise RuntimeError(
            "Gemini returned an incomplete embedding response."
        )

    vectors = []
    for item in embeddings:
        values = getattr(item, "values", None)
        if not values:
            raise RuntimeError("Gemini returned an empty embedding.")
        vectors.append([float(value) for value in values])

    return vectors


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0

    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5

    if left_norm == 0 or right_norm == 0:
        return 0.0

    return dot / (left_norm * right_norm)


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


def source_duplicate(
    db: Session,
    submission: Submission,
) -> Submission | None:
    """Detect repeated evidence by exact text or canonical source URL.

    Semantic similarity is deliberately NOT used to infer duplicates:
    different people can independently experience the same problem.
    """
    fingerprint = text_fingerprint(submission.text)
    url = canonicalize_url(submission.source_url)

    others = db.scalars(
        select(Submission).where(Submission.id != submission.id)
    ).all()

    for other in others:
        if text_fingerprint(other.text) == fingerprint:
            return other

        other_url = canonicalize_url(other.source_url)
        if url and other_url and url == other_url:
            return other

    return None


def cluster_submission(db: Session, submission: Submission) -> dict:
    existing_link = db.scalar(
        select(ClusterEvidence).where(
            ClusterEvidence.submission_id == submission.id
        )
    )

    if existing_link is not None:
        cluster = db.get(ProblemCluster, existing_link.cluster_id)
        return {
            "decision": "already_clustered",
            "cluster_id": existing_link.cluster_id,
            "title": cluster.title if cluster else None,
            "similarity": existing_link.match_confidence,
        }

    details = parse_analysis(submission)

    # Stop exact duplicates and repeated URLs from inflating evidence counts.
    
    duplicate = source_duplicate(db, submission)
    if duplicate is not None:
        duplicate_link = db.scalar(
            select(ClusterEvidence).where(
                ClusterEvidence.submission_id == duplicate.id
            )
        )

        if duplicate_link is not None:
            return {
                "decision": "duplicate_evidence",
                "duplicate_of_submission_id": duplicate.id,
                "existing_cluster_id": duplicate_link.cluster_id,
                "message": (
                    "Duplicate detected. Existing clustered evidence "
                    "was preserved; no additional evidence was added."
                ),
            }

        # If the older duplicate is still pending but this submission
        # has already been analyzed, let the analyzed record proceed.
        # It can become the canonical processed record for this complaint.
        if duplicate.analysis_status != "completed":
            pass
        else:
            return {
                "decision": "duplicate_evidence",
                "duplicate_of_submission_id": duplicate.id,
                "existing_cluster_id": None,
                "message": (
                    "Duplicate detected, but the original record is not "
                    "clustered yet. Cluster the analyzed record first."
                ),
            }

    clusters = db.scalars(
        select(ProblemCluster).order_by(desc(ProblemCluster.updated_at))
    ).all()

    # No existing clusters: create a candidate, but do not assert that
    # its source is independent unless source provenance is available.
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

    vectors = get_embeddings([query_text, *cluster_texts])
    query_vector = vectors[0]

    candidates = [
        (cosine_similarity(query_vector, vector), cluster)
        for cluster, vector in zip(clusters, vectors[1:])
    ]
    candidates.sort(key=lambda item: item[0], reverse=True)

    best_score, best_cluster = candidates[0]
    score = round(best_score, 4)

    if score >= AUTO_MATCH_THRESHOLD:
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

    if score >= REVIEW_THRESHOLD:
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
