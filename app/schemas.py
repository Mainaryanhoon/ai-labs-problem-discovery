from datetime import datetime
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_serializer,
    model_validator,
)
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

    @field_serializer("analysis_error")
    def serialize_analysis_error(self, value: str | None) -> str | None:
        if value is None:
            return None
        safe_codes = {
            "provider_not_configured",
            "rate_limited",
            "analysis_failed",
        }
        if value in safe_codes:
            return value
        normalized = value.casefold()
        if any(marker in normalized for marker in ("rate limit", "quota", "429")):
            return "rate_limited"
        if "config" in normalized:
            return "provider_not_configured"
        return "analysis_failed"


ProblemCategory = Literal[
    "Business Operations",
    "Sales & Marketing",
    "Finance & Accounting",
    "Customer Support",
    "Human Resources",
    "Productivity",
    "Software & IT",
    "Healthcare",
    "Education",
    "Other",
]


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
    evidence_quotes: list[str] = Field(default_factory=list, max_length=3)
    noise_reason: str | None = None

    rejection_reason: str | None = None
    problem_statement: str = Field(description="Concise, neutral, single-sentence problem statement.")
    affected_user: str
    context: str
    current_workaround: str | None = None
    category: ProblemCategory
    industry: str
    software_buildability: Literal["likely", "unclear", "unlikely"] = "unclear"
    software_buildability_reason: str = (
        "The submission does not provide enough information to assess this."
    )
    urgency_1_to_5: int = Field(strict=True, ge=1, le=5)
    frequency_signal_1_to_5: int = Field(strict=True, ge=1, le=5)
    evidence_strength_1_to_5: int = Field(strict=True, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rejection_details(self):
        if (
            self.signal_type in {"general_discussion", "noise"}
            and self.is_genuine_problem
        ):
            raise ValueError(
                "General discussion and noise cannot be genuine problems."
            )
        if not self.is_genuine_problem and not self.rejection_reason:
            raise ValueError(
                "A rejection reason is required for non-problem submissions."
            )
        if self.signal_type == "noise" and not self.noise_reason:
            raise ValueError("A noise reason is required for noise.")
        return self
