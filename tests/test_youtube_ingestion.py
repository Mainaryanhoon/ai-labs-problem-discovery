import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import youtube_ingestion
from app.db import Base
from app.models import SourceObservation, Submission
from app.source_roles import is_demand_role


CHANNELS = "https://www.youtube.com/@ConfiguredAnalyticsChannel"


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _video(video_id="video-123", *, transcript=None, url=None):
    return {
        "id": video_id,
        "title": "BigQuery release adds a new feature",
        "description": "A product capability walkthrough for analytics teams.",
        "url": url or f"https://www.youtube.com/watch?v={video_id}",
        "channelName": "Configured Analytics Channel",
        "uploadDate": "2026-10-28T12:00:00Z",
        "transcript": transcript,
    }


def test_youtube_disabled_without_explicit_channel_configuration(monkeypatch):
    called = False

    def collect(*_args, **_kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        youtube_ingestion,
        "get_settings",
        lambda: SimpleNamespace(youtube_channel_urls="", apify_youtube_actor_id="", youtube_api_key=""),
    )
    monkeypatch.setattr(youtube_ingestion, "_collect_apify", collect)
    db = _session()
    try:
        result = youtube_ingestion.collect_youtube(2, db)
        assert result["fetch_status"] == "not_configured"
        assert result["created"] == 0
        assert called is False
    finally:
        db.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://www.youtube.com/@Channel",
        "https://youtube.com/watch?v=video",
        "https://evil.example/@Channel",
        "https://www.youtube.com/@Channel/videos",
    ],
)
def test_youtube_rejects_non_channel_or_unapproved_urls(monkeypatch, url):
    monkeypatch.setattr(
        youtube_ingestion,
        "get_settings",
        lambda: SimpleNamespace(
            youtube_channel_urls=CHANNELS,
            apify_youtube_actor_id="streamers~youtube-scraper",
            youtube_api_key="",
        ),
    )
    db = _session()
    try:
        with pytest.raises(HTTPException) as error:
            youtube_ingestion.collect_youtube(2, db, channel_urls=[url])
        assert error.value.status_code == 422
    finally:
        db.close()


def test_youtube_stores_video_identity_role_and_available_transcript(monkeypatch):
    transcript = [{"start": 12.5, "text": "The new feature supports BigQuery."}]
    calls = []
    monkeypatch.setattr(
        youtube_ingestion,
        "get_settings",
        lambda: SimpleNamespace(
            youtube_channel_urls=CHANNELS,
            apify_youtube_actor_id="streamers~youtube-scraper",
            youtube_api_key="",
        ),
    )

    def collect(actor_id, actor_input, limit):
        calls.append((actor_id, actor_input, limit))
        return [_video(transcript=transcript)]

    monkeypatch.setattr(youtube_ingestion, "_collect_apify", collect)
    db = _session()
    try:
        first = youtube_ingestion.collect_youtube(2, db)
        second = youtube_ingestion.collect_youtube(2, db)
        assert first["created"] == 1
        assert second["created"] == 0
        assert second["skipped"] == 1
        assert calls[0][1]["startUrls"] == [{"url": CHANNELS}]
        assert calls[0][1]["downloadSubtitles"] is False
        saved = db.scalars(select(Submission)).one()
        observation = db.scalars(select(SourceObservation)).one()
        metadata = json.loads(observation.metadata_json)
        assert saved.source_url == "https://www.youtube.com/watch?v=video-123"
        assert saved.submitted_by == "Configured Analytics Channel"
        assert "The new feature supports BigQuery." in saved.text
        assert metadata["transcript_status"] == "available"
        assert metadata["transcript_segments"] == [
            {"start": 12.5, "text": "The new feature supports BigQuery."}
        ]
        assert metadata["evidence_roles"][0]["role"] == "capability"
        assert metadata["evidence_roles"][0]["evidence_quote"] in saved.text
        assert not is_demand_role("youtube")
    finally:
        db.close()


def test_youtube_records_unavailable_transcript_without_inventing_one(monkeypatch):
    monkeypatch.setattr(
        youtube_ingestion,
        "get_settings",
        lambda: SimpleNamespace(
            youtube_channel_urls=CHANNELS,
            apify_youtube_actor_id="streamers~youtube-scraper",
            youtube_api_key="",
        ),
    )
    monkeypatch.setattr(
        youtube_ingestion,
        "_collect_apify",
        lambda *_args, **_kwargs: [_video(transcript=None)],
    )
    db = _session()
    try:
        result = youtube_ingestion.collect_youtube(2, db)
        observation = db.scalars(select(SourceObservation)).one()
        metadata = json.loads(observation.metadata_json)
        assert result["created"] == 1
        assert metadata["transcript_status"] == "unavailable"
        assert metadata["transcript_retrieval"] == (
            "not_requested"
        )
        assert "transcript" not in db.scalars(select(Submission)).one().text.casefold()
    finally:
        db.close()


def test_youtube_discards_actor_items_with_non_youtube_video_urls(monkeypatch):
    monkeypatch.setattr(
        youtube_ingestion,
        "get_settings",
        lambda: SimpleNamespace(
            youtube_channel_urls=CHANNELS,
            apify_youtube_actor_id="streamers~youtube-scraper",
            youtube_api_key="",
        ),
    )
    monkeypatch.setattr(
        youtube_ingestion,
        "_collect_apify",
        lambda *_args, **_kwargs: [
            _video(url="https://unapproved.example/watch?v=video-123")
        ],
    )
    db = _session()
    try:
        result = youtube_ingestion.collect_youtube(2, db)
        assert result["created"] == 0
        assert result["skipped"] == 1
        assert db.scalars(select(Submission)).all() == []
    finally:
        db.close()