
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Submission
from app.ingestion import clean_text, normalize, PROBLEM_SIGNALS
from app.clustering import canonicalize_url

router = APIRouter(prefix="/api/ingestion", tags=["Ingestion"])

APIFY_URL = "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"


def _collect_apify(actor_id: str, actor_input: dict, limit: int) -> list[dict]:
    settings = get_settings()

    if not settings.apify_api_token:
        raise HTTPException(status_code=503, detail="Apify API token is not configured.")

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
    except (httpx.HTTPError, ValueError) as exc:
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


def _save_items(
    db: Session,
    items: list[dict],
    source_name: str,
) -> dict:
    existing = db.scalars(select(Submission)).all()

    known_urls = {
        canonicalize_url(item.source_url)
        for item in existing
        if item.source_url
    }
    known_texts = {normalize(item.text) for item in existing}

    created = duplicates = irrelevant = 0

    for item in items:
        # Actor output fields differ slightly; support common variants.
        title = clean_text(
            item.get("title") or item.get("headline") or ""
        )
        body = clean_text(
            item.get("selftext")
            or item.get("text")
            or item.get("fullText")
            or item.get("body")
            or item.get("content")
            or ""
        )

        text = f"{title}\n\n{body}".strip()
        if len(text) < 15 or not PROBLEM_SIGNALS.search(text):
            irrelevant += 1
            continue

        url = (
            item.get("permalink")
            or item.get("postUrl")
            or item.get("tweetUrl")
            or item.get("url")
            or ""
        )
        url = str(url).strip()

        # Some Reddit actors return a relative permalink.
        if url.startswith("/"):
            url = "https://www.reddit.com" + url

        if not url:
            item_id = str(item.get("id") or item.get("id_str") or "").strip()
            if source_name == "X" and item_id:
                username = item.get("username") or item.get("author")
                if isinstance(username, dict):
                    username = username.get("userName") or username.get("username")
                if username:
                    url = f"https://x.com/{username}/status/{item_id}"

        canonical_url = canonicalize_url(url) if url else None
        normalized = normalize(text)

        if (
            (canonical_url and canonical_url in known_urls)
            or (normalized and normalized in known_texts)
        ):
            duplicates += 1
            continue

        if not url:
            # Without stable provenance, don't import the item.
            irrelevant += 1
            continue

        db.add(
            Submission(
                text=text,
                source_name=source_name,
                source_url=url,
                language="en",
                analysis_status="pending",
            )
        )
        if canonical_url:
            known_urls.add(canonical_url)
        known_texts.add(normalized)
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
        "irrelevant_skipped": irrelevant,
        "analysis_status": "pending",
        "message": "Records saved; Gemini analysis has not been run.",
    }


DEFAULT_SEARCHES = [
    '"looking for a tool"',
    '"manual workaround"',
    '"any alternative to"',
    '"wish there was"',
]


@router.post("/reddit")
def ingest_reddit(
    limit: int = Query(default=10, ge=1, le=25),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    items = _collect_apify(
        settings.apify_reddit_actor_id,
        {
            "searchQueries": DEFAULT_SEARCHES,
            "sort": "new",
            "timeFilter": "month",
            "maxItems": limit,
            "maxComments": 0,
        },
        limit,
    )
    return _save_items(db, items, "Reddit")


@router.post("/x")
def ingest_x(
    limit: int = Query(default=10, ge=1, le=25),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    items = _collect_apify(
        settings.apify_x_actor_id,
        {
            "searchTerms": DEFAULT_SEARCHES,
            "sort": "Latest",
            "tweetLanguage": "en",
            "maxItems": limit,
        },
        limit,
    )
    return _save_items(db, items, "X")
