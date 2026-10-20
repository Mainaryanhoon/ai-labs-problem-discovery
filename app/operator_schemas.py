from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class EvidenceProvenanceRead(BaseModel):
    evidence_id: int
    submission_id: int
    source_name: str
    source_url: str | None
    created_at: datetime
    submission_created_at: datetime
    analysis_status: str
    clustering_status: str | None = None
    relationship_type: str
    recorded_independent: bool
    verified_independent: bool = False
    duplicate_of_submission_id: int | None = None
    duplicate_reasons: list[Literal["canonical_url", "exact_text"]] = Field(
        default_factory=list
    )
    source_observations: list["SourceObservationRead"] = Field(
        default_factory=list
    )
    scoring_status: Literal[
        "included",
        "duplicate_excluded",
        "not_scored",
    ] = "not_scored"


class SourceObservationRead(BaseModel):
    source_name: str
    source_url: str | None
    native_id: str | None = None
    community: str | None = None
    source_created_at: datetime | None = None
    timestamp_origin: Literal["source", "collection_time"]
    collected_at: datetime


class DuplicateExclusionRead(BaseModel):
    duplicate_submission_id: int
    duplicate_of_submission_id: int
    source_name: str
    source_url: str | None
    canonical_source_name: str
    canonical_source_url: str | None
    canonical_submission_created_at: datetime
    reasons: list[Literal["canonical_url", "exact_text"]]


class OpportunitySummaryRead(BaseModel):
    cluster_id: int
    title: str
    category: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    score: float | None
    evidence_level: str
    software_buildability: str = "unclear"
    software_buildability_reasons: list[str] = Field(default_factory=list)
    eligibility: str = "review_required"
    source_timestamped_records_7d: int = 0
    raw_attached_record_count: int
    analyzed_submission_count: int
    unique_evidence_count: int
    eligible_evidence_count: int = 0
    duplicate_analyzed_records_excluded: int = 0
    duplicate_exclusions: list[DuplicateExclusionRead] = Field(
        default_factory=list
    )
    distinct_source_records: int = 0
    eligible_distinct_source_record_count: int = 0
    unique_source_platform_count: int = 0
    recorded_independent_records: int = 0
    verified_independent_records: int = 0
    verified_independent_source_count: int = 0
    commercial_signal: str = "unknown"
    commercial_signal_limitation: str | None = None
    reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence_provenance: list[EvidenceProvenanceRead] = Field(
        default_factory=list
    )


class OpportunityListRead(BaseModel):
    items: list[OpportunitySummaryRead]
    total: int
    limit: int
    offset: int


class ReviewQueueItemRead(BaseModel):
    submission_id: int
    queue: Literal["analysis", "clustering"]
    status: str
    analysis_status: str
    clustering_status: str | None = None
    error_code: str | None = None
    source_name: str
    source_url: str | None
    created_at: datetime
    text_preview: str
    cluster_id: int | None = None
    action: str


class ReviewQueueRead(BaseModel):
    items: list[ReviewQueueItemRead]
    total: int
    limit: int
    offset: int
    queue: Literal["all", "analysis", "clustering"]


class PipelineStatusRead(BaseModel):
    run_lock_held: bool
    lock_lease_expired: bool
    owner_liveness_unknown: bool
    run_started_at: datetime | None
    lease_expires_at: datetime | None
    analysis_status_counts: dict[str, int]
    clustering_status_counts: dict[str, int]
    run_history_available: Literal[True] = True


class PipelineRunSummaryRead(BaseModel):
    run_id: str
    status: Literal["running", "completed", "partial", "failed"]
    requested_sources: list[str]
    stage_counts: dict
    failures: list[dict]
    started_at: datetime
    completed_at: datetime | None = None
    collected_submission_ids: list[int] = Field(default_factory=list)


class PipelineRunSubmissionRead(BaseModel):
    submission_id: int
    source_name: str
    source_url: str | None
    analysis_status: str
    clustering_status: str
    cluster_id: int | None = None
    source_metadata: dict = Field(default_factory=dict)


class PipelineRunClusterRead(BaseModel):
    cluster_id: int
    title: str
    run_submission_ids: list[int]
    run_evidence_count: int
    total_attached_evidence_count: int
    contains_evidence_outside_run: bool
    opportunity_summary: OpportunitySummaryRead


class PipelineRunRead(PipelineRunSummaryRead):
    submissions: list[PipelineRunSubmissionRead] = Field(default_factory=list)
    opportunity_clusters: list[PipelineRunClusterRead] = Field(
        default_factory=list
    )
    review_candidate_cluster_ids: list[int] = Field(default_factory=list)


class PipelineRunListRead(BaseModel):
    items: list[PipelineRunSummaryRead]
    total: int
    limit: int
    offset: int


class WeeklyRankingEntryRead(BaseModel):
    rank: int
    cluster_id: int
    title: str
    category: str | None = None
    score: float
    evidence_level: str
    source_timestamped_records_7d: int
    reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class WeeklyRankingSnapshotSummaryRead(BaseModel):
    week_start: date
    generated_at: datetime
    top_k: int
    entry_count: int


class WeeklyRankingSnapshotRead(BaseModel):
    week_start: date
    generated_at: datetime
    top_k: int
    items: list[WeeklyRankingEntryRead] = Field(default_factory=list)


class WeeklyRankingHistoryRead(BaseModel):
    items: list[WeeklyRankingSnapshotSummaryRead] = Field(default_factory=list)
    total: int
    limit: int


class WeeklyTrendPointRead(BaseModel):
    week_start: date
    rank: int | None = None
    score: float | None = None
    rank_change: int | None = None
    score_change: float | None = None


class WeeklyTrendRead(BaseModel):
    cluster_id: int
    items: list[WeeklyTrendPointRead] = Field(default_factory=list)
