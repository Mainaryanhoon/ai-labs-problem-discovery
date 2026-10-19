
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class ClusterCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    problem_statement: str = Field(min_length=10)
    affected_user: str | None = None
    category: str | None = None
    industry: str | None = None


class ClusterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    problem_statement: str
    affected_user: str | None
    category: str | None
    industry: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class EvidenceAttach(BaseModel):
    submission_id: int = Field(gt=0)
    is_independent: bool = True
    relationship_type: str = Field(
        default="supports",
        pattern="^(supports|contradicts|mentions)$",
    )
    match_confidence: float | None = Field(default=None, ge=0, le=1)
    notes: str | None = None


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    submission_id: int
    is_independent: bool
    relationship_type: str
    match_confidence: float | None
    notes: str | None
    created_at: datetime


class ClusterDetail(ClusterRead):
    evidence_count: int
    independent_evidence_count: int
    evidence: list[EvidenceRead]
