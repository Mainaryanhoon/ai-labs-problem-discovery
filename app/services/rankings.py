"""Idempotent weekly ranking snapshots and trend calculations."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import WeeklyRankingEntry, WeeklyRankingSnapshot


def current_week_start(now: datetime | None = None) -> date:
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    utc_date = timestamp.astimezone(timezone.utc).date()
    return utc_date - timedelta(days=utc_date.weekday())


def _snapshot_read(
    db: Session,
    snapshot: WeeklyRankingSnapshot,
) -> dict[str, Any]:
    generated_at = snapshot.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    entries = db.scalars(
        select(WeeklyRankingEntry)
        .where(WeeklyRankingEntry.week_start == snapshot.week_start)
        .order_by(WeeklyRankingEntry.rank.asc())
    ).all()
    return {
        "week_start": snapshot.week_start,
        "generated_at": generated_at,
        "top_k": snapshot.top_k,
        "items": [
            {
                "rank": entry.rank,
                "cluster_id": entry.cluster_id,
                "title": entry.title,
                "category": entry.category,
                "score": entry.score,
                "evidence_level": entry.evidence_level,
                "source_timestamped_records_7d": (
                    entry.source_timestamped_records_7d
                ),
                "reasons": json.loads(entry.reasons_json),
                "limitations": json.loads(entry.limitations_json),
            }
            for entry in entries
        ],
    }


def create_weekly_snapshot(
    db: Session,
    summarize: Callable[[int, Session], dict[str, Any]],
    *,
    week_start: date | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Create once per UTC week; repeated calls return the immutable snapshot."""
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    snapshot_date = week_start or current_week_start()
    existing = db.get(WeeklyRankingSnapshot, snapshot_date)
    if existing is not None:
        return _snapshot_read(db, existing)

    summaries = []
    from app.models import ProblemCluster

    cluster_ids = db.scalars(
        select(ProblemCluster.id).order_by(ProblemCluster.id.asc())
    ).all()
    for cluster_id in cluster_ids:
        summary = summarize(cluster_id, db)
        if (
            summary.get("eligibility") == "eligible"
            and isinstance(summary.get("score"), (int, float))
            and not isinstance(summary.get("score"), bool)
        ):
            summaries.append(summary)
    summaries.sort(
        key=lambda item: (-float(item["score"]), item["cluster_id"])
    )

    snapshot = WeeklyRankingSnapshot(
        week_start=snapshot_date,
        top_k=top_k,
    )
    db.add(snapshot)
    db.flush()
    for rank, summary in enumerate(summaries[:top_k], start=1):
        db.add(WeeklyRankingEntry(
            week_start=snapshot_date,
            rank=rank,
            cluster_id=summary["cluster_id"],
            title=summary["title"],
            category=summary.get("category"),
            score=float(summary["score"]),
            evidence_level=summary["evidence_level"],
            source_timestamped_records_7d=summary.get(
                "source_timestamped_records_7d",
                0,
            ),
            reasons_json=json.dumps(summary.get("reasons", [])),
            limitations_json=json.dumps(summary.get("limitations", [])),
        ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(WeeklyRankingSnapshot, snapshot_date)
        if existing is None:
            raise
        return _snapshot_read(db, existing)
    return _snapshot_read(db, snapshot)


def list_weekly_snapshots(
    db: Session,
    *,
    limit: int = 12,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 104:
        raise ValueError("limit must be between 1 and 104")
    total = db.scalar(
        select(func.count()).select_from(WeeklyRankingSnapshot)
    ) or 0
    snapshots = db.scalars(
        select(WeeklyRankingSnapshot)
        .order_by(WeeklyRankingSnapshot.week_start.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "week_start": snapshot.week_start,
                "generated_at": (
                    snapshot.generated_at.replace(tzinfo=timezone.utc)
                    if snapshot.generated_at.tzinfo is None
                    else snapshot.generated_at
                ),
                "top_k": snapshot.top_k,
                "entry_count": db.scalar(
                    select(func.count())
                    .select_from(WeeklyRankingEntry)
                    .where(WeeklyRankingEntry.week_start == snapshot.week_start)
                ) or 0,
            }
            for snapshot in snapshots
        ],
        "total": total,
        "limit": limit,
    }


def get_weekly_snapshot(
    db: Session,
    week_start: date,
) -> dict[str, Any] | None:
    snapshot = db.get(WeeklyRankingSnapshot, week_start)
    return _snapshot_read(db, snapshot) if snapshot is not None else None


def weekly_trend(
    db: Session,
    cluster_id: int,
    *,
    weeks: int = 12,
) -> dict[str, Any]:
    if isinstance(weeks, bool) or not isinstance(weeks, int) or not 1 <= weeks <= 104:
        raise ValueError("weeks must be between 1 and 104")
    snapshots = db.scalars(
        select(WeeklyRankingSnapshot)
        .order_by(WeeklyRankingSnapshot.week_start.desc())
        .limit(weeks)
    ).all()
    snapshots.reverse()
    previous_rank = None
    previous_score = None
    points = []
    for snapshot in snapshots:
        entry = db.scalar(
            select(WeeklyRankingEntry).where(
                WeeklyRankingEntry.week_start == snapshot.week_start,
                WeeklyRankingEntry.cluster_id == cluster_id,
            )
        )
        rank = entry.rank if entry is not None else None
        score = entry.score if entry is not None else None
        points.append({
            "week_start": snapshot.week_start,
            "rank": rank,
            "score": score,
            "rank_change": (
                previous_rank - rank
                if previous_rank is not None and rank is not None
                else None
            ),
            "score_change": (
                round(score - previous_score, 1)
                if previous_score is not None and score is not None
                else None
            ),
        })
        previous_rank = rank
        previous_score = score
    return {"cluster_id": cluster_id, "items": points}
