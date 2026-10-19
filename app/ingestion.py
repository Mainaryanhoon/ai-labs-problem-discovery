
import html
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Submission

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
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    """Normalize cleaned text for exact duplicate detection."""
    return " ".join(
        re.findall(r"[a-z0-9]+", clean_text(text).casefold())
    )


@router.post("/hacker-news")
def ingest_hacker_news(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Fetch recent Ask HN posts and save relevant, nonduplicate submissions."""

    try:
        response = httpx.get(
            HN_API,
            params={"tags": "ask_hn", "hitsPerPage": limit},
            timeout=25,
        )
        response.raise_for_status()
        hits = response.json().get("hits", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Hacker News source request failed.",
        ) from exc

    existing = db.scalars(
        select(Submission).where(
            Submission.source_name == "Hacker News"
        )
    ).all()

    known_urls = {
        s.source_url.rstrip("/")
        for s in existing
        if s.source_url
    }
    known_texts = {normalize(s.text) for s in existing}

    created = 0
    duplicates = 0
    irrelevant = 0

    for hit in hits:
        title = clean_text(hit.get("title") or "")
        body = clean_text(hit.get("story_text") or "")
        object_id = str(hit.get("objectID") or "").strip()

        # Exclude ordinary stories and broad monthly threads.
        if not title.lower().startswith("ask hn:"):
            irrelevant += 1
            continue

        text = f"{title}\n\n{body}".strip()
        if len(text) < 15 or not PROBLEM_SIGNALS.search(text):
            irrelevant += 1
            continue

        url = f"https://news.ycombinator.com/item?id={object_id}"
        normalized = normalize(text)

        if url.rstrip("/") in known_urls or normalized in known_texts:
            duplicates += 1
            continue

        submission = Submission(
            text=text,
            source_name="Hacker News",
            source_url=url,
            language="en",
            analysis_status="pending",
        )
        db.add(submission)
        known_urls.add(url.rstrip("/"))
        known_texts.add(normalized)
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
        "irrelevant_skipped": irrelevant,
        "analysis_status": "pending",
        "message": "Records saved; Gemini analysis has not been run.",
    }


@router.post("/stack-exchange")
def ingest_stack_exchange(
    limit: int = Query(default=30, ge=1, le=100),
    site: str = Query(default="stackoverflow", min_length=2, max_length=40),
    db: Session = Depends(get_db),
):
    """Import recent Stack Exchange questions as problem candidates."""

    try:
        response = httpx.get(
            "https://api.stackexchange.com/2.3/questions",
            params={
                "site": site,
                "pagesize": limit,
                "order": "desc",
                "sort": "creation",
                "filter": "withbody",
            },
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        questions = payload.get("items", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Stack Exchange request failed.",
        ) from exc

    existing = db.scalars(
        select(Submission).where(
            Submission.source_name == f"Stack Exchange:{site}"
        )
    ).all()

    known_urls = {
        s.source_url.rstrip("/")
        for s in existing
        if s.source_url
    }
    known_texts = {normalize(s.text) for s in existing}

    created = 0
    duplicates = 0

    for question in questions:
        title = clean_text(question.get("title") or "")
        body = clean_text(question.get("body") or "")
        text = f"{title}\n\n{body}".strip()

        if len(text) < 30:
            continue

        url = question.get("link", "").strip()
        normalized = normalize(text)

        if not url or url.rstrip("/") in known_urls or normalized in known_texts:
            duplicates += 1
            continue

        # Keep source metadata in the text for now, until dedicated
        # structured source-metadata fields are added.
        tags = ", ".join(question.get("tags", []))
        evidence = (
            f"\n\n[Source metadata: site={site}; "
            f"score={question.get('score', 0)}; "
            f"answers={question.get('answer_count', 0)}; "
            f"views={question.get('view_count', 0)}; "
            f"tags={tags}; "
            f"created_at_epoch={question.get('creation_date', '')}]"
        )

        db.add(
            Submission(
                text=text + evidence,
                source_name=f"Stack Exchange:{site}",
                source_url=url,
                language="en",
                analysis_status="pending",
            )
        )
        known_urls.add(url.rstrip("/"))
        known_texts.add(normalized)
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
        "source": f"Stack Exchange:{site}",
        "fetched": len(questions),
        "created": created,
        "duplicates_skipped": duplicates,
        "analysis_status": "pending",
        "quota_remaining": payload.get("quota_remaining"),
        "message": "Questions imported; Gemini analysis has not been run.",
    }
