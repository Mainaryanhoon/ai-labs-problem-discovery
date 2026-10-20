
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(
        String(100), default="manual", nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    language: Mapped[str] = mapped_column(String(20), default="en", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    analysis_status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False
    )
    analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    frequency_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    cluster_links: Mapped[list["ClusterEvidence"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
    )


class ProblemCluster(Base):
    __tablename__ = "problem_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    problem_statement: Mapped[str] = mapped_column(Text, nullable=False)
    affected_user: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="candidate", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    evidence_links: Mapped[list["ClusterEvidence"]] = relationship(
        back_populates="cluster",
        cascade="all, delete-orphan",
    )


class ClusterEvidence(Base):
    __tablename__ = "cluster_evidence"
    __table_args__ = (
        UniqueConstraint(
            "cluster_id",
            "submission_id",
            name="uq_cluster_submission",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("problem_clusters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_independent: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        String(30), default="supports", nullable=False
    )
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    cluster: Mapped["ProblemCluster"] = relationship(
        back_populates="evidence_links"
    )
    submission: Mapped["Submission"] = relationship(
        back_populates="cluster_links"
    )


class PipelineSubmissionState(Base):
    __tablename__ = "pipeline_submission_states"

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    clustering_status: Mapped[str] = mapped_column(
        String(30), default="not_attempted", nullable=False
    )
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("problem_clusters.id", ondelete="SET NULL"),
        nullable=True,
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class PipelineRunLock(Base):
    __tablename__ = "pipeline_run_locks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(20), default="running", nullable=False
    )
    requested_sources: Mapped[str] = mapped_column(Text, nullable=False)
    stage_counts: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    failures: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PipelineRunSubmission(Base):
    __tablename__ = "pipeline_run_submissions"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SourceRecord(Base):
    """Structured provenance for one collected submission.

    This separate table allows additive initialization without altering
    existing submission columns. One row per submission is supported.
    """

    __tablename__ = "source_records"

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    native_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    community: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    timestamp_origin: Mapped[str] = mapped_column(
        String(20), default="collection_time", nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SourceObservation(Base):
    """One source-specific observation, including deduplicated cross-posts."""

    __tablename__ = "source_observations"
    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "native_id",
            name="uq_source_observation_native_id",
        ),
        UniqueConstraint(
            "source_name",
            "canonical_url",
            name="uq_source_observation_canonical_url",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    native_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    community: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timestamp_origin: Mapped[str] = mapped_column(
        String(20), default="collection_time", nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class WeeklyRankingSnapshot(Base):
    __tablename__ = "weekly_ranking_snapshots"

    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)


class WeeklyRankingEntry(Base):
    __tablename__ = "weekly_ranking_entries"
    __table_args__ = (
        UniqueConstraint(
            "week_start",
            "cluster_id",
            name="uq_weekly_ranking_cluster",
        ),
    )

    week_start: Mapped[date] = mapped_column(
        ForeignKey("weekly_ranking_snapshots.week_start", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_level: Mapped[str] = mapped_column(String(50), nullable=False)
    source_timestamped_records_7d: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
    limitations_json: Mapped[str] = mapped_column(Text, nullable=False)


class SubmissionProviderMetadata(Base):
    __tablename__ = "submission_provider_metadata"

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
