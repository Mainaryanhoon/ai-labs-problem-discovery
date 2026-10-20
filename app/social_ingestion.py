
import json
import threading
from typing import Any
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import (
    SourceObservation,
    Submission,
    SubmissionProviderMetadata,
)
from app.clustering import canonicalize_url
from app.provenance import (
    known_native_ids,
    known_observation_urls,
    normalize_subreddit,
    parse_source_reference,
    record_source_observation,
    record_source_provenance,
)
from app.ingestion import (
    PROBLEM_SIGNALS,
    clean_text,
    normalize,
    parse_source_datetime,
)

router = APIRouter(prefix="/api/ingestion", tags=["Ingestion"])

APIFY_URL = "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"


def _collect_apify(
    actor_id: str,
    actor_input: dict[str, Any],
    limit: int,
) -> list[Any]:
    settings = get_settings()

    if not settings.apify_api_token.strip():
        raise HTTPException(status_code=503, detail="Apify API token is not configured.")
    if not actor_id or not actor_id.strip():
        raise HTTPException(status_code=503, detail="Apify Actor is not configured.")

    try:
        response = httpx.post(
            APIFY_URL.format(actor_id=actor_id),
            headers={"Authorization": f"Bearer {settings.apify_api_token}"},
            params={"limit": limit, "timeout": 120},
            json=actor_input,
            timeout=130,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                "Apify collection timed out; check the Actor run status "
                "before retrying."
            ),
        ) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise HTTPException(
                status_code=503,
                detail="Apify rate limit reached; retry later.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail="Apify collection failed. Check the Actor configuration and run logs.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Apify returned malformed JSON.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Apify collection failed. Check the Actor configuration and run logs.",
        ) from exc

    if not isinstance(payload, list):
        raise HTTPException(
            status_code=502,
            detail="Apify returned an unexpected dataset format.",
        )

    return payload[:limit]


def _metadata_flag(
    item: dict[str, Any],
    *,
    boolean_keys: tuple[str, ...],
    object_keys: tuple[str, ...] = (),
    identifier_keys: tuple[str, ...] = (),
) -> bool | None:
    saw_false = False
    for key in boolean_keys:
        value = item.get(key)
        if value is True:
            return True
        if value is False:
            saw_false = True
    if any(item.get(key) is not None for key in object_keys):
        return True
    if any(item.get(key) not in (None, "", 0) for key in identifier_keys):
        return True
    return False if saw_false else None


def _provider_metadata(item: dict[str, Any], source_name: str) -> dict:
    if source_name == "X":
        author = item.get("author")
        author_name = (
            author.get("userName")
            if isinstance(author, dict)
            else None
        )
        metadata = {
            "is_repost": _metadata_flag(
                item,
                boolean_keys=("isRetweet", "isRetweeted", "isRepost"),
                object_keys=("retweetedTweet", "retweeted_status"),
            ),
            "is_reply": _metadata_flag(
                item,
                boolean_keys=("isReply",),
                identifier_keys=(
                    "inReplyToStatusId",
                    "inReplyToTweetId",
                    "inReplyToUserId",
                ),
            ),
            "is_quote": _metadata_flag(
                item,
                boolean_keys=("isQuote",),
                object_keys=("quotedTweet", "quoted_status"),
            ),
            "author": (
                author_name.strip()[:200]
                if isinstance(author_name, str) and author_name.strip()
                else None
            ),
            "author_is_business_account": (
                author.get("isBusinessAccount")
                if isinstance(author, dict)
                and isinstance(author.get("isBusinessAccount"), bool)
                else None
            ),
        }
    else:
        metadata = {
            "is_repost": _metadata_flag(
                item,
                boolean_keys=("isCrosspost",),
                object_keys=("crosspostParent", "crosspost_parent"),
            ),
            "is_reply": None,
            "is_quote": None,
            "author": (
                item.get("author")[:200]
                if isinstance(item.get("author"), str)
                else None
            ),
        }
    return {
        key: value
        for key, value in metadata.items()
        if value is not None
    }


def _save_items(
    db: Session,
    items: list[Any],
    source_name: str,
    *,
    per_community_limits: dict[str, int] | None = None,
) -> dict:
    existing = db.scalars(select(Submission)).all()

    submissions_by_url = {
        canonical_url: item
        for item in existing
        if (canonical_url := canonicalize_url(item.source_url))
    }
    submissions_by_text = {
        normalized: item
        for item in existing
        if (normalized := normalize(item.text))
    }
    for observation, submission in db.execute(
        select(SourceObservation, Submission).join(
            Submission,
            Submission.id == SourceObservation.submission_id,
        )
    ).all():
        submissions_by_url.setdefault(
            observation.canonical_url,
            submission,
        )
    known_source_urls = known_observation_urls(db, source_name)
    known_source_urls.update(
        canonical_url
        for item in existing
        if item.source_name == source_name
        if (canonical_url := canonicalize_url(item.source_url))
    )
    known_native = known_native_ids(db, source_name)

    created = duplicates = irrelevant = 0
    observations_linked = 0
    bounded_skipped = 0
    community_counts: dict[str, int] = {}
    submission_ids = []
    submission_metadata = {}

    for item in items:
        if not isinstance(item, dict):
            irrelevant += 1
            continue

        if source_name == "Reddit":
            if item.get("itemType") != "post":
                irrelevant += 1
                continue
            title = clean_text(item.get("title"))
            body = clean_text(item.get("selftext"))
            raw_url = item.get("link")
            submitted_by = item.get("author")
            created_at_value = item.get("publishingDate")
            language = "und"
            item_id = item.get("postId") or item.get("id")
        elif source_name == "X":
            item_type = item.get("type")
            if item_type is not None and item_type != "tweet":
                irrelevant += 1
                continue
            title = ""
            body = clean_text(item.get("text"))
            raw_url = item.get("url")
            author = item.get("author")
            submitted_by = (
                author.get("userName")
                if isinstance(author, dict)
                else None
            )
            created_at_value = item.get("createdAt")
            language = item.get("lang")
            item_id = item.get("id")
        else:
            irrelevant += 1
            continue

        text = f"{title}\n\n{body}".strip()
        if len(text) < 15 or not PROBLEM_SIGNALS.search(text):
            irrelevant += 1
            continue

        url = raw_url.strip() if isinstance(raw_url, str) else ""
        canonical_url = canonicalize_url(url) if url else None
        normalized = normalize(text)
        reference = parse_source_reference(source_name, url, item_id)
        community = reference.community
        if source_name == "Reddit":
            community = community or normalize_subreddit(item.get("subreddit"))
            if per_community_limits is not None:
                if community not in per_community_limits:
                    irrelevant += 1
                    continue
                if (
                    community_counts.get(community, 0)
                    >= per_community_limits[community]
                ):
                    bounded_skipped += 1
                    continue

        if (
            canonical_url in known_source_urls
            or (reference.native_id and reference.native_id in known_native)
        ):
            duplicates += 1
            continue

        if not canonical_url:
            # Without stable provenance, don't import the item.
            irrelevant += 1
            continue

        duplicate_submission = (
            submissions_by_url.get(canonical_url)
            or submissions_by_text.get(normalized)
        )
        metadata = _provider_metadata(item, source_name)
        if duplicate_submission is not None:
            record_source_observation(
                db,
                duplicate_submission,
                source_name=source_name,
                source_url=url,
                canonical_url=canonical_url,
                native_id=reference.native_id,
                community=community,
                source_created_at=parse_source_datetime(created_at_value),
                metadata=metadata,
            )
            duplicates += 1
            observations_linked += 1
            known_source_urls.add(canonical_url)
            if reference.native_id:
                known_native.add(reference.native_id)
            continue

        submission = Submission(
            text=text,
            source_name=source_name,
            source_url=url,
            submitted_by=(
                submitted_by.strip()
                if isinstance(submitted_by, str) and submitted_by.strip()
                else None
            ),
            language=(
                language.strip()
                if isinstance(language, str) and language.strip()
                else "und"
            ),
            analysis_status="pending",
        )
        source_created_at = parse_source_datetime(created_at_value)
        if source_created_at is not None:
            submission.created_at = source_created_at
        db.add(submission)
        db.flush()
        record_source_provenance(
            db,
            submission,
            native_id=reference.native_id,
            community=community,
            source_url=url,
            canonical_url=canonical_url,
            source_created_at=source_created_at,
            metadata=metadata,
        )
        if reference.native_id:
            known_native.add(reference.native_id)
        submission_ids.append(submission.id)
        if metadata:
            submission_metadata[str(submission.id)] = metadata
            db.add(SubmissionProviderMetadata(
                submission_id=submission.id,
                source_name=source_name,
                metadata_json=json.dumps(metadata),
            ))
        submissions_by_url.setdefault(canonical_url, submission)
        if normalized:
            submissions_by_text.setdefault(normalized, submission)
        known_source_urls.add(canonical_url)
        if source_name == "Reddit" and community:
            community_counts[community] = (
                community_counts.get(community, 0) + 1
            )
        created += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Could not save {source_name} submissions.",
        ) from exc

    return {
        "source": source_name,
        "fetched": len(items),
        "created": created,
        "duplicates_skipped": duplicates,
        "observations_linked": observations_linked,
        "irrelevant_skipped": irrelevant,
        "bounded_skipped": bounded_skipped,
        "analysis_status": "pending",
        "message": "Records saved; Gemini analysis has not been run.",
        "submission_ids": submission_ids,
        "submission_metadata": submission_metadata,
    }


DEFAULT_SEARCHES = [
    '"looking for a tool"',
    '"manual workaround"',
    '"any alternative to"',
    '"wish there was"',
]

DEFAULT_X_SEARCH_TERMS = [
    '"struggling" OR "frustrating" OR "manual work" OR "workaround"',
]


REDDIT_SUBREDDIT_SHORTLIST = (
    "smallbusiness",
    "Entrepreneur",
    "SaaS",
    "startups",
    "productivity",
    "automation",
    "ecommerce",
    "shopify",
    "sysadmin",
    "devops",
)
MAX_SUBREDDITS = 10
X_MIN_QUERY_ITEMS = 50
_X_RUN_LOCK = threading.Lock()


def _reddit_subreddit_urls(subreddits: list[str]) -> list[dict[str, str]]:
    names = []
    for raw_name in subreddits:
        name = normalize_subreddit(raw_name)
        if name is None:
            raise HTTPException(status_code=422, detail="Invalid subreddit name.")
        if name not in names:
            names.append(name)
    if not names or len(names) > MAX_SUBREDDITS:
        raise HTTPException(
            status_code=422,
            detail=f"Provide between 1 and {MAX_SUBREDDITS} subreddits.",
        )
    return [
        {"url": f"https://www.reddit.com/r/{name}/new/"}
        for name in names
    ]


def collect_reddit(
    limit: int,
    db: Session,
    subreddits: list[str] | None = None,
) -> dict:
    settings = get_settings()
    actor_input: dict[str, Any] = {
        "sort": "new",
        "timeFilter": "month",
        "maxItems": limit,
        "maxComments": 0,
    }
    per_community_limits = None
    if subreddits is None:
        actor_input["searchQueries"] = DEFAULT_SEARCHES
    else:
        actor_input["startUrls"] = _reddit_subreddit_urls(subreddits)
        names = list(dict.fromkeys(
            normalize_subreddit(name)
            for name in subreddits
        ))
        valid_names = [name for name in names if name is not None]
        base_limit, remainder = divmod(limit, len(valid_names))
        per_community_limits = {
            name: base_limit + int(index < remainder)
            for index, name in enumerate(valid_names)
        }
    items = _collect_apify(
        settings.apify_reddit_actor_id,
        actor_input,
        limit,
    )
    result = _save_items(
        db,
        items,
        "Reddit",
        per_community_limits=per_community_limits,
    )
    result["per_community_limits"] = per_community_limits
    return result


def collect_x(limit: int, db: Session) -> dict:
    if limit != X_MIN_QUERY_ITEMS:
        raise HTTPException(
            status_code=422,
            detail=f"X collection requires exactly {X_MIN_QUERY_ITEMS} items.",
        )
    if not _X_RUN_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="An X collection is already running in this worker.",
        )
    try:
        settings = get_settings()
        items = _collect_apify(
            settings.apify_x_actor_id,
            {
                "searchTerms": DEFAULT_X_SEARCH_TERMS,
                "sort": "Latest",
                "tweetLanguage": "en",
                "maxItems": limit,
            },
            limit,
        )
        return _save_items(db, items, "X")
    finally:
        _X_RUN_LOCK.release()


@router.post("/reddit")
def ingest_reddit(
    limit: int = Query(default=10, ge=1, le=25),
    subreddits: list[str] | None = Query(default=None, max_length=10),
    db: Session = Depends(get_db),
):
    result = collect_reddit(limit, db, subreddits)
    result.pop("submission_ids")
    result.pop("submission_metadata", None)
    return result


@router.post("/x")
def ingest_x(
    limit: int = Query(
        default=X_MIN_QUERY_ITEMS,
        ge=X_MIN_QUERY_ITEMS,
        le=X_MIN_QUERY_ITEMS,
    ),
    db: Session = Depends(get_db),
):
    result = collect_x(limit, db)
    result.pop("submission_ids")
    result.pop("submission_metadata", None)
    return result
