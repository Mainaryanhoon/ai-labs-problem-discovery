
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
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
        Boolean, default=True, nullable=False
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
