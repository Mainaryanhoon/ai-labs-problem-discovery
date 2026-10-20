
import html
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clustering import canonicalize_url, normalize_text
from app.db import get_db
from app.models import SourceObservation, Submission
from app.provenance import (
    known_observation_urls,
    known_native_ids,
    parse_source_reference,
    record_source_observation,
    record_source_provenance,
)

router = APIRouter(prefix="/api/ingestion", tags=["Ingestion"])

HN_API = "https://hn.algolia.com/api/v1/search_by_date"

# Initial relevance filter; we'll refine it using reviewed examples.
PROBLEM_SIGNALS = re.compile(
    r"\b(problem|struggle|frustrat\w*|difficult|hard to|"
    r"workaround|alternative to|wish there|need a tool|"
    r"manual process|anyone else|how do you|pain point|"
    r"expensive|broken|annoying|can't|cannot)\b",
    re.IGNORECASE,
)

def clean_text(text: str) -> str:
    """Decode HTML and remove markup before analysis."""
    if not isinstance(text, str):
        return ""
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    """Normalize cleaned text for exact duplicate detection."""
    return normalize_text(clean_text(text))


def parse_source_datetime(value: Any) -> datetime | None:
    """Parse supported provider timestamps without inventing missing values."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                parsed = datetime.strptime(
                    value.strip(), "%a %b %d %H:%M:%S %z %Y"
                )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
    except (OverflowError, OSError, ValueError):
        return None
    return None


def _get_json(url: str, *, params: dict[str, Any], source: str) -> Any:
    for attempt in range(2):
        try:
            response = httpx.get(url, params=params, timeout=25)
        except httpx.TimeoutException as exc:
            if attempt == 0:
                continue
            raise HTTPException(
                status_code=504,
                detail=f"{source} request timed out.",
            ) from exc
        except httpx.ConnectError as exc:
            if attempt == 0:
                continue
            raise HTTPException(
                status_code=502,
                detail=f"{source} source request failed.",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"{source} source request failed.",
            ) from exc

        if response.status_code == 429:
            raise HTTPException(
                status_code=503,
                detail=f"{source} rate limit reached; retry later.",
            )
        if response.status_code >= 500 and attempt == 0:
            continue
        try:
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"{source} source request failed.",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"{source} returned malformed JSON.",
            ) from exc

    raise HTTPException(
        status_code=502,
        detail=f"{source} source request failed.",
    )


def _existing_source_records(
    db: Session,
) -> tuple[dict[str, Submission], dict[str, Submission]]:
    by_url: dict[str, Submission] = {}
    by_text: dict[str, Submission] = {}
    existing = db.scalars(select(Submission)).all()
    for submission in existing:
        if canonical_url := canonicalize_url(submission.source_url):
            by_url.setdefault(canonical_url, submission)
        if normalized := normalize(submission.text):
            by_text.setdefault(normalized, submission)
    for observation, submission in db.execute(
        select(SourceObservation, Submission).join(
            Submission,
            Submission.id == SourceObservation.submission_id,
        )
    ).all():
        by_url.setdefault(observation.canonical_url, submission)
    return by_url, by_text


def _known_source_urls(db: Session, source_name: str) -> set[str]:
    urls = known_observation_urls(db, source_name)
    urls.update(
        canonical_url
        for submission in db.scalars(
            select(Submission).where(Submission.source_name == source_name)
        )
        if (canonical_url := canonicalize_url(submission.source_url))
    )
    return urls


def collect_hacker_news(limit: int, db: Session) -> dict:
    """Fetch and persist a bounded batch of relevant Ask HN posts."""

    payload = _get_json(
        HN_API,
        params={"tags": "ask_hn", "hitsPerPage": limit},
        source="Hacker News",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
        raise HTTPException(
            status_code=502,
            detail="Hacker News returned an unexpected response format.",
        )
    hits = payload["hits"]
    submissions_by_url, submissions_by_text = _existing_source_records(db)
    known_source_urls = _known_source_urls(db, "Hacker News")
    known_ids = known_native_ids(db, "Hacker News")

    created = 0
    duplicates = 0
    observations_linked = 0
    irrelevant = 0
    submission_ids = []

    for hit in hits:
        if not isinstance(hit, dict):
            irrelevant += 1
            continue
        title = clean_text(hit.get("title") or "")
        body = clean_text(hit.get("story_text") or "")
        object_id = hit.get("objectID")
        if isinstance(object_id, bool) or not isinstance(object_id, (str, int)):
            object_id = ""
        object_id = str(object_id).strip()

        # Exclude ordinary stories and broad monthly threads.
        if not title.lower().startswith("ask hn:"):
            irrelevant += 1
            continue

        text = f"{title}\n\n{body}".strip()
        if len(text) < 15 or not PROBLEM_SIGNALS.search(text):
            irrelevant += 1
            continue

        if not object_id or not re.fullmatch(r"\d+", object_id):
            irrelevant += 1
            continue

        url = f"https://news.ycombinator.com/item?id={object_id}"
        normalized = normalize(text)
        canonical_url = canonicalize_url(url)
        reference = parse_source_reference("Hacker News", url, object_id)

        if not canonical_url:
            irrelevant += 1
            continue
        if (
            canonical_url in known_source_urls
            or (reference.native_id and reference.native_id in known_ids)
        ):
            duplicates += 1
            continue

        duplicate_submission = (
            submissions_by_url.get(canonical_url)
            or submissions_by_text.get(normalized)
        )
        if duplicate_submission is not None:
            record_source_observation(
                db,
                duplicate_submission,
                source_name="Hacker News",
                source_url=url,
                canonical_url=canonical_url,
                native_id=reference.native_id,
                community=reference.community,
                source_created_at=parse_source_datetime(
                    hit.get("created_at_i")
                ),
            )
            duplicates += 1
            observations_linked += 1
            known_source_urls.add(canonical_url)
            if reference.native_id:
                known_ids.add(reference.native_id)
            continue

        submission = Submission(
            text=text,
            source_name="Hacker News",
            source_url=url,
            submitted_by=(
                hit["author"].strip()
                if isinstance(hit.get("author"), str)
                and hit["author"].strip()
                else None
            ),
            language="und",
            analysis_status="pending",
        )
        source_created_at = parse_source_datetime(hit.get("created_at_i"))
        if source_created_at is not None:
            submission.created_at = source_created_at
        db.add(submission)
        db.flush()
        record_source_provenance(
            db,
            submission,
            native_id=reference.native_id,
            community=reference.community,
            source_url=url,
            canonical_url=canonical_url,
            source_created_at=source_created_at,
        )
        submission_ids.append(submission.id)
        submissions_by_url.setdefault(canonical_url, submission)
        submissions_by_text.setdefault(normalized, submission)
        known_source_urls.add(canonical_url)
        if reference.native_id:
            known_ids.add(reference.native_id)
        created += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not save Hacker News submissions.",
        ) from exc

    return {
        "source": "Hacker News",
        "fetched": len(hits),
        "created": created,
        "duplicates_skipped": duplicates,
        "observations_linked": observations_linked,
        "irrelevant_skipped": irrelevant,
        "analysis_status": "pending",
        "message": "Records saved; Gemini analysis has not been run.",
        "submission_ids": submission_ids,
    }


@router.post("/hacker-news")
def ingest_hacker_news(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    result = collect_hacker_news(limit, db)
    result.pop("submission_ids")
    return result


def collect_stack_exchange(limit: int, site: str, db: Session) -> dict:
    """Import recent Stack Exchange questions as problem candidates."""

    payload = _get_json(
        "https://api.stackexchange.com/2.3/questions",
        params={
            "site": site,
            "pagesize": limit,
            "order": "desc",
            "sort": "creation",
            "filter": "withbody",
        },
        source="Stack Exchange",
    )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail="Stack Exchange returned an unexpected response format.",
        )
    if payload.get("error_id") is not None:
        raise HTTPException(
            status_code=502,
            detail="Stack Exchange reported a source API error.",
        )
    if payload.get("backoff") is not None:
        raise HTTPException(
            status_code=503,
            detail="Stack Exchange requested a rate-limit backoff; retry later.",
        )
    if not isinstance(payload.get("items"), list):
        raise HTTPException(
            status_code=502,
            detail="Stack Exchange returned an unexpected response format.",
        )
    questions = payload["items"]
    source_name = f"Stack Exchange:{site}"
    submissions_by_url, submissions_by_text = _existing_source_records(db)
    known_source_urls = _known_source_urls(db, source_name)
    known_ids = known_native_ids(db, source_name)

    created = 0
    duplicates = 0
    observations_linked = 0
    irrelevant = 0
    submission_ids = []

    for question in questions:
        if not isinstance(question, dict):
            irrelevant += 1
            continue
        title = clean_text(question.get("title") or "")
        body = clean_text(question.get("body") or "")
        text = f"{title}\n\n{body}".strip()

        if len(text) < 30 or not title or not body:
            irrelevant += 1
            continue

        raw_url = question.get("link")
        url = raw_url.strip() if isinstance(raw_url, str) else ""
        canonical_url = canonicalize_url(url)
        normalized = normalize(text)
        reference = parse_source_reference(
            source_name,
            url,
            question.get("question_id"),
        )

        if not canonical_url:
            irrelevant += 1
            continue
        if (
            canonical_url in known_source_urls
            or (reference.native_id and reference.native_id in known_ids)
        ):
            duplicates += 1
            continue

        duplicate_submission = (
            submissions_by_url.get(canonical_url)
            or submissions_by_text.get(normalized)
        )
        if duplicate_submission is not None:
            source_created_at = parse_source_datetime(
                question.get("creation_date")
            )
            record_source_observation(
                db,
                duplicate_submission,
                source_name=source_name,
                source_url=url,
                canonical_url=canonical_url,
                native_id=reference.native_id,
                community=reference.community,
                source_created_at=source_created_at,
            )
            duplicates += 1
            observations_linked += 1
            known_source_urls.add(canonical_url)
            if reference.native_id:
                known_ids.add(reference.native_id)
            continue

        # Keep source metadata in the text for now, until dedicated
        # structured source-metadata fields are added.
        raw_tags = question.get("tags")
        tags = ", ".join(
            tag for tag in raw_tags if isinstance(tag, str)
        ) if isinstance(raw_tags, list) else ""
        owner = question.get("owner")
        submitted_by = (
            owner.get("display_name")
            if isinstance(owner, dict)
            else None
        )
        evidence = (
            f"\n\n[Source metadata: site={site}; "
            f"source_id={question.get('question_id', '')}; "
            f"score={question.get('score', 0)}; "
            f"answers={question.get('answer_count', 0)}; "
            f"views={question.get('view_count', 0)}; "
            f"tags={tags}; "
            f"created_at_epoch={question.get('creation_date', '')}]"
        )

        submission = Submission(
            text=text + evidence,
            source_name=source_name,
            source_url=url,
            submitted_by=(
                submitted_by.strip()
                if isinstance(submitted_by, str) and submitted_by.strip()
                else None
            ),
            language="und",
            analysis_status="pending",
        )
        source_created_at = parse_source_datetime(question.get("creation_date"))
        if source_created_at is not None:
            submission.created_at = source_created_at
        db.add(submission)
        db.flush()
        record_source_provenance(
            db,
            submission,
            native_id=reference.native_id,
            community=reference.community,
            source_url=url,
            canonical_url=canonical_url,
            source_created_at=source_created_at,
        )
        submission_ids.append(submission.id)
        submissions_by_url.setdefault(canonical_url, submission)
        submissions_by_text.setdefault(normalized, submission)
        known_source_urls.add(canonical_url)
        if reference.native_id:
            known_ids.add(reference.native_id)
        created += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not save Stack Exchange submissions.",
        ) from exc

    return {
        "source": source_name,
        "fetched": len(questions),
        "created": created,
        "duplicates_skipped": duplicates,
        "observations_linked": observations_linked,
        "irrelevant_skipped": irrelevant,
        "analysis_status": "pending",
        "quota_remaining": payload.get("quota_remaining"),
        "message": "Questions imported; Gemini analysis has not been run.",
        "submission_ids": submission_ids,
    }


@router.post("/stack-exchange")
def ingest_stack_exchange(
    limit: int = Query(default=30, ge=1, le=100),
    site: str = Query(
        default="stackoverflow",
        min_length=2,
        max_length=40,
        pattern=r"^[a-z0-9.-]+$",
    ),
    db: Session = Depends(get_db),
):
    result = collect_stack_exchange(limit, site, db)
    result.pop("submission_ids")
    return result
