"""YouTube niche channels + podcast-style uploads via Apify (no YouTube Data API)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.clustering import canonicalize_url
from app.config import get_settings
from app.db import get_db
from app.docs_filter_helpers import fetch_not_before, should_fetch_new_item
from app.ingestion import PROBLEM_SIGNALS, clean_text, parse_source_datetime
from app.models import Submission
from app.provenance import (
    known_native_ids,
    known_observation_urls,
    newest_source_created_at,
    record_source_provenance,
)
from app.social_ingestion import _collect_apify
from app.source_roles import build_role_claim

router = APIRouter(prefix="/api/ingestion", tags=["Ingestion"])

MAX_YOUTUBE_CHANNELS = 25



def _is_channel_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.hostname not in {
        "youtube.com",
        "www.youtube.com",
    } or parsed.query or parsed.fragment:
        return False
    path = parsed.path.rstrip("/")
    return bool(
        (path.startswith("/@") and len(path) > 2 and "/" not in path[2:])
        or (path.startswith("/channel/UC") and "/" not in path[9:])
    )


def _channel_urls() -> list[str]:
    settings = get_settings()
    raw = settings.youtube_channel_urls.strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


def _transcript(item: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    value = item.get("transcript") or item.get("captions") or item.get("subtitles")
    if isinstance(value, str) and value.strip():
        return clean_text(value), []
    if not isinstance(value, list):
        return "", []
    chunks: list[str] = []
    segments: list[dict[str, Any]] = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            chunks.append(clean_text(entry))
            continue
        if not isinstance(entry, dict):
            continue
        text = entry.get("text") or entry.get("caption")
        if not isinstance(text, str) or not text.strip():
            continue
        text = clean_text(text)
        chunks.append(text)
        start = entry.get("start") or entry.get("startTime") or entry.get("offset")
        segments.append({"start": start, "text": text})
    return "\n".join(chunks), segments


def _item_text(item: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
    title = clean_text(
        item.get("title")
        or item.get("name")
        or ""
    )
    description = clean_text(
        item.get("text")
        or item.get("description")
        or item.get("desc")
        or ""
    )
    url = (
        item.get("url")
        or item.get("videoUrl")
        or item.get("link")
    )
    if isinstance(url, str) and url.strip():
        url = url.strip()
    else:
        video_id = item.get("id") or item.get("videoId")
        url = (
            f"https://www.youtube.com/watch?v={video_id}"
            if isinstance(video_id, str) and video_id.strip()
            else None
        )
    channel = (
        item.get("channelName")
        or item.get("channelTitle")
        or item.get("channel")
    )
    channel_name = (
        channel.strip()[:200]
        if isinstance(channel, str) and channel.strip()
        else None
    )
    return title, description, url, channel_name


def _native_id(item: dict[str, Any], url: str | None) -> str | None:
    for key in ("id", "videoId", "video_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    if url:
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        if (
            parsed.scheme == "https"
            and parsed.hostname in {"youtube.com", "www.youtube.com"}
            and parsed.path == "/watch"
        ):
            value = parse_qs(parsed.query).get("v", [None])[0]
            return value[:120] if isinstance(value, str) and value else None
    return None


def _is_video_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"youtube.com", "www.youtube.com"}
        and parsed.path == "/watch"
        and bool(parse_qs(parsed.query).get("v", [None])[0])
    )


def _resolve_channel_id(youtube_client: Any, url: str) -> str | None:
    """Resolve a YouTube channel URL (handle or /channel/ID) to a channel ID."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path.startswith("/channel/UC"):
        return path[len("/channel/"):]
    # Handle form @handle — use search or forHandle param (Data API v3)
    handle = path.lstrip("/@")
    try:
        resp = youtube_client.channels().list(
            forHandle=handle,
            part="id",
            maxResults=1,
        ).execute()
        items = resp.get("items", [])
        if items:
            return items[0]["id"]
    except Exception:
        pass
    return None


def _channel_upload_playlist(channel_id: str) -> str:
    """Convert a channel ID to its uploads playlist ID."""
    # YouTube uploads playlist: replace 'UC' prefix with 'UU'
    if channel_id.startswith("UC"):
        return "UU" + channel_id[2:]
    return ""


def _collect_youtube_data_api(
    *,
    urls: list[str],
    limit: int,
    db: Session,
    api_key: str,
    max_per_channel: int = 10,
) -> dict[str, Any]:
    """Collect YouTube videos via the official YouTube Data API v3.

    Falls back gracefully to an empty result if googleapiclient is unavailable.
    """
    try:
        from googleapiclient.discovery import build as yt_build  # type: ignore
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail=(
                "google-api-python-client is not installed. "
                "Run: .venv/bin/pip install google-api-python-client"
            ),
        )

    youtube = yt_build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    known_ids = known_native_ids(db, "youtube")
    known_urls_set = known_observation_urls(db, "youtube")
    newest = newest_source_created_at(db, "youtube")
    not_before = fetch_not_before(newest)

    created = 0
    skipped = 0
    outside_budget = 0
    submission_ids: list[int] = []
    metadata_by_id: dict[str, dict[str, Any]] = {}

    for channel_url in urls:
        if created >= limit:
            break
        channel_id = _resolve_channel_id(youtube, channel_url)
        if not channel_id:
            skipped += 1
            continue
        uploads_pl = _channel_upload_playlist(channel_id)
        if not uploads_pl:
            skipped += 1
            continue

        # Fetch recent video IDs from uploads playlist
        video_ids: list[str] = []
        page_token: str | None = None
        while len(video_ids) < max_per_channel:
            remaining = max_per_channel - len(video_ids)
            pl_resp = youtube.playlistItems().list(
                playlistId=uploads_pl,
                part="contentDetails",
                maxResults=min(50, remaining),
                **({"pageToken": page_token} if page_token else {}),
            ).execute()
            for item in pl_resp.get("items", []):
                vid = item.get("contentDetails", {}).get("videoId")
                if vid and vid not in known_ids:
                    video_ids.append(vid)
            page_token = pl_resp.get("nextPageToken")
            if not page_token:
                break

        if not video_ids:
            continue

        # Fetch snippet + contentDetails for those video IDs
        vids_resp = youtube.videos().list(
            id=",".join(video_ids[:50]),
            part="snippet,contentDetails",
        ).execute()

        for item in vids_resp.get("items", []):
            if created >= limit:
                break
            native = item.get("id", "")
            snippet = item.get("snippet", {})
            title = clean_text(snippet.get("title", ""))
            description = clean_text(snippet.get("description", ""))
            channel_name = snippet.get("channelTitle", "")
            published_raw = snippet.get("publishedAt")
            url_vid = f"https://www.youtube.com/watch?v={native}" if native else None

            if not url_vid or not native:
                skipped += 1
                continue

            canonical = canonicalize_url(url_vid) or url_vid
            if native in known_ids or canonical in known_urls_set:
                skipped += 1
                continue

            text = f"{title}\n\n{description}".strip()
            if len(text) < 40:
                skipped += 1
                continue

            is_capabilityish = any(
                token in text.casefold()
                for token in (
                    "release", "what's new", "announcing", "ga ", "ga4",
                    "preview", "deprecat", "bigquery", "experiment",
                    "forecast", "tag manager", "gtm", "ab test", "a/b", "cro",
                )
            )
            if not PROBLEM_SIGNALS.search(text) and not is_capabilityish:
                skipped += 1
                continue

            published = parse_source_datetime(published_raw)
            if not should_fetch_new_item(published, newest_stored=newest):
                outside_budget += 1
                continue

            capability_terms = (
                "release", "announcing", "new feature", "product demo",
                "preview", "deprecat", "launch", "capability",
            )
            capability_item = any(
                term in f"{title} {description}".casefold()
                for term in capability_terms
            )
            role = "capability" if capability_item else "context"
            role_claim = build_role_claim(
                "youtube", role,
                source_text=text,
                evidence_quote=title,
                justification=(
                    "Video title/description identifies a product capability or launch."
                    if capability_item
                    else "Video is retained as opt-in context; no demand is inferred."
                ),
            )
            submission = Submission(
                text=text[:12000],
                source_name="youtube",
                source_url=url_vid,
                submitted_by=channel_name or None,
                language="en",
            )
            if published is not None:
                submission.created_at = published
            db.add(submission)
            db.flush()
            record_source_provenance(
                db, submission,
                native_id=native,
                community=(channel_name[:100] if channel_name else None),
                source_url=url_vid,
                canonical_url=canonical,
                source_created_at=published,
                metadata={
                    "channel": channel_name,
                    "title": title,
                    "description": description,
                    "kind": "youtube_data_api_v3",
                    "transcript_status": "unavailable",
                    "transcript_retrieval": "not_requested",
                    "transcript_text": None,
                    "transcript_segments": [],
                    "evidence_roles": [role_claim] if role_claim else [],
                },
            )
            known_ids.add(native)
            known_urls_set.add(canonical)
            created += 1
            submission_ids.append(submission.id)
            metadata_by_id[str(submission.id)] = {
                "video_id": native,
                "channel": channel_name,
                "title": title,
                "transcript_status": "unavailable",
                "evidence_role": role if role_claim else "unknown",
                "collection_method": "youtube_data_api_v3",
            }

    db.commit()
    return {
        "source": "youtube",
        "collection_method": "youtube_data_api_v3",
        "created": created,
        "skipped": skipped,
        "outside_budget": outside_budget,
        "fetch_not_before": not_before.isoformat(),
        "channels": urls,
        "submission_ids": submission_ids,
        "submission_metadata": metadata_by_id,
    }


def collect_youtube(
    limit: int = 20,
    db: Session | None = None,
    *,
    channel_urls: list[str] | None = None,

) -> dict[str, Any]:
    """Scrape configured channel uploads through Apify YouTube actor."""
    if db is None:
        raise HTTPException(status_code=500, detail="Database session required.")

    settings = get_settings()
    urls = channel_urls or _channel_urls()
    if not urls:
        return {
            "source": "youtube",
            "attempted": False,
            "fetch_status": "not_configured",
            "created": 0,
            "skipped": 0,
            "channels": [],
            "submission_ids": [],
            "submission_metadata": {},
            "message": (
                "YouTube pilot is not configured; provide explicit approved "
                "channel URLs and the existing actor configuration."
            ),
        }
    urls = list(dict.fromkeys(urls))
    if len(urls) > MAX_YOUTUBE_CHANNELS or any(
        not _is_channel_url(url) for url in urls
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Configure at most 10 HTTPS YouTube channel URLs using a "
                "channel handle or channel ID."
            ),
        )

    actor_id = settings.apify_youtube_actor_id.strip()
    youtube_api_key = settings.youtube_api_key.strip()

    # ------------------------------------------------------------------ #
    # PRIMARY: Google YouTube Data API v3 (when YOUTUBE_API_KEY is set)   #
    # FALLBACK: Apify scraper actor                                        #
    # ------------------------------------------------------------------ #
    if youtube_api_key:
        return _collect_youtube_data_api(
            urls=urls,
            limit=limit,
            db=db,
            api_key=youtube_api_key,
            max_per_channel=settings.youtube_max_results_per_channel,
        )

    # Apify fallback — cap pull above persist limit so keyword filter still fills.
    fetch_limit = min(100, max(limit * 3, limit))
    start_urls = [{"url": url} for url in urls]
    items = _collect_apify(
        actor_id,
        {
            "startUrls": start_urls,
            "maxResults": max(1, fetch_limit // max(1, len(urls))),
            "maxResultsShorts": 0,
            "maxResultStreams": 0,
            "downloadSubtitles": False,
            "subtitlesLanguage": "en",
        },
        fetch_limit,
    )

    known_ids = known_native_ids(db, "youtube")
    known_urls = known_observation_urls(db, "youtube")
    newest = newest_source_created_at(db, "youtube")
    not_before = fetch_not_before(newest)
    created = 0
    skipped = 0
    outside_budget = 0
    submission_ids: list[int] = []
    metadata_by_id: dict[str, dict[str, Any]] = {}

    for item in items:
        if created >= limit:
            break
        if not isinstance(item, dict):
            skipped += 1
            continue

        title, description, url, channel_name = _item_text(item)
        native = _native_id(item, url)
        canonical = canonicalize_url(url) or url if url else ""
        if (native and native in known_ids) or canonical in known_urls:
            skipped += 1
            continue
        transcript, transcript_segments = _transcript(item)
        text = f"{title}\n\n{description}\n\n{transcript}".strip()
        if not url or not _is_video_url(url) or not native or len(text) < 40:
            skipped += 1
            continue

        is_capabilityish = any(
            token in text.casefold()
            for token in (
                "release",
                "what's new",
                "announcing",
                "ga ",
                "ga4",
                "preview",
                "deprecat",
                "bigquery",
                "experiment",
                "forecast",
                "tag manager",
                "gtm",
                "ab test",
                "a/b",
                "cro",
            )
        )
        if not PROBLEM_SIGNALS.search(text) and not is_capabilityish:
            skipped += 1
            continue

        published = parse_source_datetime(
            item.get("date")
            or item.get("uploadDate")
            or item.get("publishedAt")
            or item.get("publishedDate")
        )
        if not should_fetch_new_item(published, newest_stored=newest):
            outside_budget += 1
            continue
        capability_terms = (
            "release", "announcing", "new feature", "product demo",
            "preview", "deprecat", "launch", "capability",
        )
        capability_item = any(
            term in f"{title} {description}".casefold()
            for term in capability_terms
        )
        role = "capability" if capability_item else "context"
        role_claim = build_role_claim(
            "youtube",
            role,
            source_text=text,
            evidence_quote=title,
            justification=(
                "Video title/description identifies a product capability or launch."
                if capability_item
                else "Video is retained as opt-in context; no demand is inferred."
            ),
        )
        submission = Submission(
            text=text[:12000],
            source_name="youtube",
            source_url=url,
            submitted_by=channel_name,
            language="en",
        )
        if published is not None:
            submission.created_at = published
        db.add(submission)
        db.flush()
        record_source_provenance(
            db,
            submission,
            native_id=native,
            community=(channel_name[:100] if channel_name else None),
            source_url=url,
            canonical_url=canonical,
            source_created_at=published,
            metadata={
                "channel": channel_name,
                "title": title,
                "description": description,
                "kind": "youtube_apify",
                "actor_id": actor_id,
                "transcript_status": (
                    "available" if transcript else "unavailable"
                ),
                "transcript_retrieval": "not_requested",
                "transcript_text": transcript or None,
                "transcript_segments": transcript_segments,
                "evidence_roles": [role_claim] if role_claim else [],
            },
        )
        if native:
            known_ids.add(native)
        known_urls.add(canonical)
        created += 1
        submission_ids.append(submission.id)
        metadata_by_id[str(submission.id)] = {
            "video_id": native,
            "channel": channel_name,
            "title": title,
            "transcript_status": (
                "available" if transcript else "unavailable"
            ),
            "evidence_role": role if role_claim else "unknown",
        }

    db.commit()
    return {
        "source": "youtube",
        "created": created,
        "skipped": skipped,
        "outside_budget": outside_budget,
        "fetch_not_before": not_before.isoformat(),
        "channels": urls,
        "submission_ids": submission_ids,
        "submission_metadata": metadata_by_id,
    }


@router.post("/youtube")
def ingest_youtube(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return collect_youtube(limit, db)
