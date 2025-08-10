from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    leagues: List[str] = Field(default_factory=lambda: ["nba", "nfl"], alias="LEAGUES")
    espn_poll_seconds: int = Field(default=5, alias="ESPN_POLL_SECONDS")

    # LLM
    use_llm: bool = Field(default=True, alias="USE_LLM")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_model: str = Field(default="claude-3-5-sonnet-20240620", alias="LLM_MODEL")

    # Media
    stream_url: str | None = Field(default=None, alias="STREAM_URL")
    buffer_dir: Path = Field(default=Path("data/buffer"), alias="BUFFER_DIR")
    clips_dir: Path = Field(default=Path("data/clips"), alias="CLIPS_DIR")
    pre_roll_seconds: int = Field(default=8, alias="PRE_ROLL_SECONDS")
    post_roll_seconds: int = Field(default=6, alias="POST_ROLL_SECONDS")

    # Storage
    enable_s3: bool = Field(default=False, alias="ENABLE_S3")
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    s3_prefix: str = Field(default="highlights/", alias="S3_PREFIX")

    # Server
    server_host: str = Field(default="0.0.0.0", alias="SERVER_HOST")
    server_port: int = Field(default=8000, alias="SERVER_PORT")

    # Reddit (optional)
    reddit_enabled: bool = Field(default=False, alias="REDDIT_ENABLED")
    reddit_client_id: str | None = Field(default=None, alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str | None = Field(default=None, alias="REDDIT_CLIENT_SECRET")
    reddit_user_agent: str | None = Field(default="bigplays-agent/0.1", alias="REDDIT_USER_AGENT")


settings = Settings()  # Singleton-like convenience

