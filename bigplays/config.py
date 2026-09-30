from __future__ import annotations

from pathlib import Path
from typing import List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    leagues: List[str] = Field(default_factory=lambda: ["nba", "nfl", "ncaaf", "mlb"], alias="LEAGUES")
    espn_poll_seconds: int = Field(default=5, alias="ESPN_POLL_SECONDS")
    mlb_highlights_enabled: bool = Field(default=False, alias='MLB_HIGHLIGHTS_ENABLED')
    mlb_highlights_poll_seconds: int = Field(default=60, ge=30, le=600, alias='MLB_HIGHLIGHTS_POLL_SECONDS')

    # LLM
    use_llm: bool = Field(default=True, alias="USE_LLM")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_model: str = Field(default="claude-3-5-sonnet-20240620", alias="LLM_MODEL")

    resolver_base_url: str = Field(default="http://127.0.0.1:3000", alias="RESOLVER_BASE_URL")
    resolver_api_key: str = Field(default="", alias="RESOLVER_API_KEY")
    agent_dir: Path = Field(default=Path('data/agent'), alias='AGENT_DIR')
    agent_max_games: int = Field(default=2, ge=1, le=8, alias='AGENT_MAX_GAMES')
    agent_buffer_minutes: int = Field(default=30, ge=5, le=180, alias='AGENT_BUFFER_MINUTES')
    agent_buffer_max_mb: int = Field(default=2048, ge=128, le=16384, alias='AGENT_BUFFER_MAX_MB')
    agent_pre_roll_seconds: int = Field(default=12, ge=3, le=60, alias='AGENT_PRE_ROLL_SECONDS')
    agent_post_roll_seconds: int = Field(default=4, ge=1, le=30, alias='AGENT_POST_ROLL_SECONDS')
    scoreboard_ocr_bin: str = Field(default='data/tools/scoreboard-ocr', alias='SCOREBOARD_OCR_BIN')

    # Media
    stream_url: str | None = Field(default=None, alias="STREAM_URL")
    buffer_dir: Path = Field(default=Path("data/buffer"), alias="BUFFER_DIR")
    clips_dir: Path = Field(default=Path("data/clips"), alias="CLIPS_DIR")
    pre_roll_seconds: int = Field(default=8, alias="PRE_ROLL_SECONDS")
    post_roll_seconds: int = Field(default=6, alias="POST_ROLL_SECONDS")

    # Storage
    database_path: Path | None = Field(default=None, alias='DATABASE_PATH')
    enable_s3: bool = Field(default=False, alias="ENABLE_S3")
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    s3_prefix: str = Field(default="highlights/", alias="S3_PREFIX")

    # Demo mode (replays scripted plays with synthetic clips when no live games exist)
    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    demo_league: Literal['all', 'nba', 'nfl'] = Field(default='all', alias='DEMO_LEAGUE')
    demo_dataset: Literal['highlights', 'nfl-2026-week3'] = Field(default='highlights', alias='DEMO_DATASET')
    demo_clips_dir: Path = Field(default=Path("data/demo_clips"), alias="DEMO_CLIPS_DIR")
    demo_min_interval: float = Field(default=7.0, alias="DEMO_MIN_INTERVAL")
    demo_max_interval: float = Field(default=14.0, alias="DEMO_MAX_INTERVAL")

    # Server
    server_host: str = Field(default="0.0.0.0", alias="SERVER_HOST")
    server_port: int = Field(default=8000, alias="SERVER_PORT")

    # Reddit (optional)
    # off = no gate (clip on the initial filter); hype = cut, hold, publish on Reddit fan-hype
    # approval (true/1/on also mean hype); legacy = the older approve-before-cut OAuth/browser judge.
    social_clip_gate: Literal['off', 'hype', 'legacy'] = Field(default='hype', alias='SOCIAL_CLIP_GATE')
    social_hype_window_seconds: int = Field(default=30, ge=10, le=180, alias='SOCIAL_HYPE_WINDOW_SECONDS')
    social_hype_pre_seconds: int = Field(default=5, ge=0, le=60, alias='SOCIAL_HYPE_PRE_SECONDS')
    social_hype_fallback_seconds: int = Field(default=90, ge=15, le=900, alias='SOCIAL_HYPE_FALLBACK_SECONDS')
    social_hype_pending_ttl_hours: float = Field(default=6.0, ge=0.1, le=72, alias='SOCIAL_HYPE_PENDING_TTL_HOURS')
    social_hype_threshold: float = Field(default=0.45, ge=0, le=1, alias='SOCIAL_HYPE_THRESHOLD')
    social_hype_strict_threshold: float = Field(default=0.7, ge=0, le=1, alias='SOCIAL_HYPE_STRICT_THRESHOLD')
    social_hype_llm_timeout_seconds: float = Field(default=20.0, ge=3, le=60, alias='SOCIAL_HYPE_LLM_TIMEOUT_SECONDS')
    reddit_source: Literal['api', 'browser'] = Field(default='api', alias='REDDIT_SOURCE')
    reddit_enabled: bool = Field(default=False, alias="REDDIT_ENABLED")
    reddit_client_id: str | None = Field(default=None, alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str | None = Field(default=None, alias="REDDIT_CLIENT_SECRET")
    reddit_refresh_token: str | None = Field(default=None, alias='REDDIT_REFRESH_TOKEN')
    reddit_poll_seconds: int = Field(default=15, ge=10, le=120, alias='REDDIT_POLL_SECONDS')
    social_llm_provider: Literal['ollama', 'anthropic'] = Field(default='ollama', alias='SOCIAL_LLM_PROVIDER')
    social_llm_model: str = Field(default='qwen3:4b', alias='SOCIAL_LLM_MODEL')
    ollama_base_url: str = Field(default='http://127.0.0.1:11434', alias='OLLAMA_BASE_URL')
    social_llm_calls_per_hour: int = Field(default=40, ge=1, le=200, alias='SOCIAL_LLM_CALLS_PER_HOUR')
    reddit_user_agent: str | None = Field(default="bigplays-agent/0.1", alias="REDDIT_USER_AGENT")
    # Reddit public RSS game-thread reactions (no credentials; official .rss feeds only)
    reddit_rss_enabled: bool = Field(default=True, alias='REDDIT_RSS_ENABLED')
    reddit_rss_user_agent: str = Field(default='BigPlays/0.1 (personal sports reactions reader)',
                                       alias='REDDIT_RSS_USER_AGENT')
    reddit_rss_min_interval_seconds: float = Field(default=30.0, ge=15, le=600, alias='REDDIT_RSS_MIN_INTERVAL_SECONDS')
    reddit_rss_max_games: int = Field(default=3, ge=1, le=10, alias='REDDIT_RSS_MAX_GAMES')
    reddit_rss_max_threads: int = Field(default=3, ge=1, le=20, alias='REDDIT_RSS_MAX_THREADS')
    reddit_rss_state_path: Path = Field(default=Path('data/social/reddit_rss.json'), alias='REDDIT_RSS_STATE_PATH')

    @field_validator('social_clip_gate', mode='before')
    @classmethod
    def _gate_mode(cls, value):
        return clip_gate_mode(value)


def clip_gate_mode(value) -> str:
    """Normalize SOCIAL_CLIP_GATE: booleans and on/off words map to 'hype'/'off'."""
    if isinstance(value, bool) or value is None:
        return 'hype' if value else 'off'
    text = str(value).strip().lower()
    if text in ('', 'false', '0', 'off', 'no', 'none', 'disabled'):
        return 'off'
    if text == 'legacy':
        return 'legacy'
    if text in ('true', '1', 'on', 'yes', 'hype', 'enabled'):
        return 'hype'
    raise ValueError('SOCIAL_CLIP_GATE must be off, hype or legacy')


settings = Settings()  # Singleton-like convenience


def gate_mode() -> str:
    """The active clip gate ('off' | 'hype' | 'legacy'), tolerant of tests assigning booleans."""
    return clip_gate_mode(settings.social_clip_gate)
