"""Structured provenance and aggregate source-yield helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ClusterEvidence,
    SourceObservation,
    SourceRecord,
    Submission,
)
from app.ranking import ACTIONABLE_SIGNAL_TYPES

_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")
_REDDIT_ID = re.compile(r"^[a-z0-9]{3,12}$")
_MAX_NATIVE_ID_LENGTH = 120


@dataclass(frozen=True)
class SourceReference:
    native_id: str | None
    community: str | None


def clean_native_id(value: Any, *, source_name: str = "") -> str | None:
    """Normalize a source-native ID or return None for unusable values."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    native_id = str(value).strip()
    if not native_id or len(native_id) > _MAX_NATIVE_ID_LENGTH:
        return None
    if source_name == "Reddit":
        native_id = re.sub(r"^t3_", "", native_id, flags=re.IGNORECASE).lower()
        return native_id if _REDDIT_ID.fullmatch(native_id) else None
    if source_name in {"X", "Hacker News"} or source_name.startswith(
        "Stack Exchange:"
    ):
        return native_id if native_id.isdecimal() else None
    return native_id


def normalize_subreddit(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    cleaned = name.strip().strip("/")
    cleaned = re.sub(r"^r/", "", cleaned, flags=re.IGNORECASE)
    if not _SUBREDDIT.fullmatch(cleaned):
        return None
    return cleaned.casefold()


def parse_source_reference(
    source_name: str,
    url: str | None,
    item_id: Any = None,
) -> SourceReference:
    """Extract provider identity from fields and known canonical URL forms."""
    native_id = clean_native_id(item_id, source_name=source_name)
    community = None
    try:
        parsed = urlsplit(url or "")
    except ValueError:
        return SourceReference(native_id=native_id, community=None)

    host = (parsed.hostname or "").casefold()
    path_parts = [part for part in parsed.path.split("/") if part]
    if source_name == "Reddit":
        if host in {
            "reddit.com",
            "www.reddit.com",
            "old.reddit.com",
            "new.reddit.com",
        }:
            for index, part in enumerate(path_parts[:-2]):
                if part.casefold() == "r" and index + 2 < len(path_parts):
                    community = normalize_subreddit(path_parts[index + 1])
                    if path_parts[index + 2].casefold() == "comments":
                        candidate = path_parts[index + 3] if index + 3 < len(path_parts) else ""
                        native_id = native_id or clean_native_id(
                            candidate,
                            source_name="Reddit",
                        )
                    break
        elif host in {"redd.it", "www.redd.it"} and path_parts:
            native_id = native_id or clean_native_id(
                path_parts[0],
                source_name="Reddit",
            )
    elif source_name == "X":
        if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            for index, part in enumerate(path_parts[:-1]):
                if part.casefold() == "status":
                    native_id = native_id or clean_native_id(
                        path_parts[index + 1],
                        source_name="X",
                    )
                    break
    elif source_name == "Hacker News":
        if host in {"news.ycombinator.com", "www.news.ycombinator.com"}:
            query = dict(
                pair.split("=", 1) if "=" in pair else (pair, "")
                for pair in parsed.query.split("&")
                if pair
            )
            native_id = native_id or clean_native_id(
                query.get("id"),
                source_name="Hacker News",
            )
    elif source_name.startswith("Stack Exchange:"):
        community = source_name.partition(":")[2] or None
        if "questions" in path_parts:
            index = path_parts.index("questions")
            if index + 1 < len(path_parts):
                native_id = native_id or clean_native_id(
                    path_parts[index + 1],
                    source_name=source_name,
                )
    return SourceReference(native_id=native_id, community=community)


def known_native_ids(db: Session, source_name: str) -> set[str]:
    records = {
        native_id
        for native_id in db.scalars(
            select(SourceRecord.native_id).where(
                SourceRecord.source_name == source_name,
                SourceRecord.native_id.is_not(None),
            )
        )
        if native_id
    }
    records.update(
        native_id
        for native_id in db.scalars(
            select(SourceObservation.native_id).where(
                SourceObservation.source_name == source_name,
                SourceObservation.native_id.is_not(None),
            )
        )
        if native_id
    )
    return records


def known_observation_urls(db: Session, source_name: str) -> set[str]:
    return {
        canonical_url
        for canonical_url in db.scalars(
            select(SourceObservation.canonical_url).where(
                SourceObservation.source_name == source_name
            )
        )
        if canonical_url
    }


def record_source_observation(
    db: Session,
    submission: Submission,
    *,
    source_name: str,
    source_url: str,
    canonical_url: str,
    native_id: str | None,
    community: str | None,
    source_created_at: datetime | None,
    metadata: dict[str, Any] | None = None,
) -> SourceObservation:
    observation = SourceObservation(
        submission_id=submission.id,
        source_name=source_name,
        source_url=source_url,
        canonical_url=canonical_url,
        native_id=native_id,
        community=community,
        metadata_json=json.dumps(metadata) if metadata else None,
        source_created_at=source_created_at,
        timestamp_origin=(
            "source" if source_created_at is not None else "collection_time"
        ),
    )
    db.add(observation)
    return observation


def record_source_provenance(
    db: Session,
    submission: Submission,
    *,
    native_id: str | None,
    community: str | None,
    source_url: str,
    canonical_url: str,
    source_created_at: datetime | None,
    metadata: dict[str, Any] | None = None,
) -> SourceRecord:
    """Attach provenance in the submission's current transaction."""
    record = SourceRecord(
        submission_id=submission.id,
        source_name=submission.source_name,
        native_id=native_id,
        community=community,
        timestamp_origin=(
            "source" if source_created_at is not None else "collection_time"
        ),
    )
    db.add(record)
    record_source_observation(
        db,
        submission,
        source_name=submission.source_name,
        source_url=source_url,
        canonical_url=canonical_url,
        native_id=native_id,
        community=community,
        source_created_at=source_created_at,
        metadata=metadata,
    )
    return record


def _actionable_verdict(analysis_json: str | None) -> bool | None:
    if not analysis_json:
        return None
    try:
        analysis = json.loads(analysis_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(analysis, dict):
        return None
    signal_type = analysis.get("signal_type")
    if not isinstance(signal_type, str):
        return None
    if signal_type not in ACTIONABLE_SIGNAL_TYPES:
        return False
    value = analysis.get("is_genuine_problem")
    return value if isinstance(value, bool) else None


def community_yield(db: Session) -> list[dict[str, Any]]:
    """Return collected, analyzed, actionable and clustered counts by source."""
    rows = db.execute(
        select(SourceObservation, Submission).join(
            Submission,
            Submission.id == SourceObservation.submission_id,
        )
    ).all()
    represented = {
        (observation.source_name, observation.submission_id)
        for observation, _submission in rows
    }
    legacy_rows = db.execute(
        select(SourceRecord, Submission).join(
            Submission,
            Submission.id == SourceRecord.submission_id,
        )
    ).all()
    rows.extend(
        (record, submission)
        for record, submission in legacy_rows
        if (record.source_name, record.submission_id) not in represented
    )
    clustered_ids = set(db.scalars(select(ClusterEvidence.submission_id)))
    buckets: dict[tuple[str, str | None], dict[str, int]] = {}
    for provenance, submission in rows:
        key = (provenance.source_name, provenance.community)
        counts = buckets.setdefault(
            key,
            {"collected": 0, "analyzed": 0, "genuine": 0, "clustered": 0},
        )
        counts["collected"] += 1
        if submission.analysis_status == "completed":
            verdict = _actionable_verdict(submission.analysis_json)
            if verdict is not None:
                counts["analyzed"] += 1
                counts["genuine"] += int(verdict)
        if submission.id in clustered_ids:
            counts["clustered"] += 1

    results = []
    for (source_name, community), counts in buckets.items():
        analyzed = counts["analyzed"]
        results.append({
            "source_name": source_name,
            "community": community,
            **counts,
            "genuine_rate": (
                round(counts["genuine"] / analyzed, 4) if analyzed else None
            ),
        })
    results.sort(
        key=lambda row: (
            row["genuine_rate"] is None,
            -(row["genuine_rate"] or 0),
            -row["collected"],
            row["source_name"],
            row["community"] or "",
        )
    )
    return results
