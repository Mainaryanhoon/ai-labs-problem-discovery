from typing import Literal

from pydantic import BaseModel, Field, field_validator


PipelineSource = Literal["hacker_news", "stack_exchange", "reddit", "x"]


class PipelineRunRequest(BaseModel):
    sources: list[PipelineSource] = Field(
        min_length=1,
        max_length=4,
    )
    hacker_news_limit: int = Field(default=10, ge=1, le=100)
    stack_exchange_limit: int = Field(default=10, ge=1, le=100)
    stack_exchange_site: str = Field(
        default="stackoverflow",
        min_length=2,
        max_length=40,
        pattern=r"^[a-z0-9.-]+$",
    )
    reddit_limit: int = Field(default=5, ge=1, le=25)
    reddit_subreddits: list[str] | None = Field(default=None, max_length=10)
    x_limit: int = Field(
        default=50,
        ge=50,
        le=50,
        description=(
            "Fixed at the X actor's documented minimum of 50 results per query."
        ),
    )
    analysis_limit: int = Field(default=10, ge=1, le=30)
    clustering_limit: int = Field(default=10, ge=1, le=30)
    retry_needs_review: bool = False
    retry_failed_clustering: bool = False
    retry_failed_analysis: bool = False
    recover_interrupted_run: bool = Field(
        default=False,
        description=(
            "Deprecated compatibility field. API-based stale-lock recovery "
            "is disabled; use the documented offline procedure."
        ),
    )

    @field_validator("reddit_subreddits")
    @classmethod
    def validate_reddit_subreddits(
        cls,
        names: list[str] | None,
    ) -> list[str] | None:
        if names is None:
            return None
        from app.provenance import normalize_subreddit

        cleaned = []
        for name in names:
            normalized = normalize_subreddit(name)
            if normalized is None:
                raise ValueError("Invalid subreddit name.")
            if normalized not in cleaned:
                cleaned.append(normalized)
        if not cleaned:
            raise ValueError("Provide at least one subreddit or omit the field.")
        return cleaned

    @field_validator("sources")
    @classmethod
    def reject_duplicate_sources(
        cls,
        sources: list[PipelineSource],
    ) -> list[PipelineSource]:
        if len(sources) != len(set(sources)):
            raise ValueError("Each source may be selected only once.")
        return sources
