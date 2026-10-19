from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from typing import Literal



class SubmissionCreate(BaseModel):
    text: str = Field(min_length=15, max_length=12000)
    source_name: str = Field(default="manual", min_length=1, max_length=100)
    source_url: HttpUrl | None = None
    submitted_by: str | None = Field(default=None, max_length=200)
    language: str = Field(default="en", min_length=2, max_length=20)


class SubmissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    text: str
    source_name: str
    source_url: str | None
    submitted_by: str | None
    language: str
    created_at: datetime
    analysis_status: str
    analysis_json: str | None
    model_name: str | None
    prompt_version: str | None
    analysis_error: str | None


class ProblemExtraction(BaseModel):
    is_genuine_problem: bool
    
    signal_type: Literal[
        "pain_point",
        "explicit_tool_request",
        "workaround_or_manual_work",
        "feature_gap",
        "buying_signal",
        "general_discussion",
        "noise",
    ]
    solution_request: str | None = None
    willingness_to_pay_signal: Literal[
        "explicit", "suggested", "none", "unclear"
    ] = "unclear"
    evidence_quotes: list[str] = Field(default_factory=list)
    noise_reason: str | None = None

    rejection_reason: str | None = None
    problem_statement: str = Field(description="Concise, neutral, single-sentence problem statement.")
    affected_user: str
    context: str
    current_workaround: str | None = None
    category: str
    industry: str
    urgency_1_to_5: int = Field(ge=1, le=5)
    frequency_signal_1_to_5: int = Field(ge=1, le=5)
    evidence_strength_1_to_5: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
