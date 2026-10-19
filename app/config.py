from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    database_url: str = "sqlite:///./data/problem_discovery.db"
    apify_api_token: str = ""
    apify_actor_id: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    
    apify_reddit_actor_id: str = "anyxsolutions~reddit-scraper"
    apify_x_actor_id: str = "apidojo~tweet-scraper"


@lru_cache
def get_settings() -> Settings:
    return Settings()
