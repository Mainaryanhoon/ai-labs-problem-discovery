from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LeadProcessed(Base):
    """One row per submission already sent through lead extraction."""

    __tablename__ = "lead_processed"
    submission_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    submission_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    source_host: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    who_pays: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))
    why_now: Mapped[str] = mapped_column(Text, nullable=False)
    first_customer_route: Mapped[str] = mapped_column(Text, nullable=False)
    smallest_first_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_type: Mapped[str] = mapped_column(String(20), nullable=False)  # service|product
    tatvic_fit_note: Mapped[str | None] = mapped_column(Text)
    evidence_quotes_json: Mapped[str] = mapped_column(Text, nullable=False)
    india_quote: Mapped[str | None] = mapped_column(Text)
    components_json: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    weight_version: Mapped[str] = mapped_column(String(20), nullable=False)
    corroborating_urls_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="new")  # new|accepted|rejected
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IdeaLog(Base):
    """Cross-week memory so the same idea is not re-surfaced unchanged."""

    __tablename__ = "idea_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    times_surfaced: Mapped[int] = mapped_column(Integer, default=0)
    last_surfaced_score: Mapped[float | None] = mapped_column(Float)


class LeadFeedback(Base):
    __tablename__ = "lead_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)  # accepted|rejected
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(100), default="reviewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
