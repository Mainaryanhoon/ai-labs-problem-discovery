from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LiteLLM / OpenAI-compatible local or remote chat endpoint
    litellm_base_url: str = "http://127.0.0.1:4000/v1"
    litellm_api_key: str = ""
    litellm_model: str = "qwen3.8-27b"
    litellm_embedding_model: str = ""
    litellm_max_tokens: int = Field(default=4096, ge=256, le=32000)

    # Legacy Gemini (optional fallback if LiteLLM key empty and Gemini set)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    database_url: str = "sqlite:///./data/problem_discovery.db"
    apify_api_token: str = ""
    apify_actor_id: str = ""
    service_api_key: str = ""
    submission_rate_limit_per_minute: int = Field(default=0, ge=0)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    apify_reddit_actor_id: str = "anyxsolutions~reddit-scraper"
    apify_x_actor_id: str = "apidojo~tweet-scraper"
    # YouTube — primary: Google YouTube Data API v3 (set YOUTUBE_API_KEY).
    # Fallback: Apify scraper actor when youtube_api_key is empty.
    youtube_api_key: str = ""
    apify_youtube_actor_id: str = "streamers~youtube-scraper"
    # Comma-separated HTTPS channel URLs (handles or /channel/UC…). Required.
    youtube_channel_urls: str = ""
    # Max videos fetched per channel per run (Data API path).
    youtube_max_results_per_channel: int = Field(default=10, ge=1, le=50)

    # Articles: RSS discovery, then Firecrawl, then Playwright.
    firecrawl_api_key: str = ""
    firecrawl_base_url: str = "https://api.firecrawl.dev"
    # Comma-separated https feeds. Empty uses the built-in allowlist.
    article_feed_urls: str = ""

    # CEO monthly Top-6 report (Gmail API optional — report always saved locally)
    ceo_email: str = ""
    gmail_from_email: str = ""
    gmail_client_secrets_file: str = ""
    gmail_token_file: str = "data/secrets/gmail_token.json"
    report_public_base_url: str = "http://localhost:5173"
    monthly_top_k: int = Field(default=6, ge=1, le=10)
    report_output_dir: str = "data/reports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
