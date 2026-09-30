from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class League(str, Enum):
    NBA = "nba"
    NFL = "nfl"
    NCAAF = "ncaaf"
    MLB = "mlb"


class HighlightReason(str, Enum):
    LEAD_CHANGE = "lead_change"
    BIG_SCORING_PLAY = "big_scoring_play"
    CLUTCH_TIME = "clutch_time"
    SOCIAL_SPIKE = "social_spike"
    MOMENTUM_SWING = "momentum_swing"
    LLM_VIRAL = "llm_viral"


@dataclass
class Game:
    league: League
    game_id: str
    home_team: str
    away_team: str
    start_time_utc: datetime
    status: str  # pre, in, post


@dataclass
class ScoringEvent:
    game_id: str
    league: League
    occurred_utc: datetime
    description: str
    home_score: int
    away_score: int
    period: str
    clock: str


@dataclass
class SocialSignal:
    platform: str
    occurred_utc: datetime
    text: str
    score: float  # crude engagement/volume score
    metadata: Dict[str, str]


@dataclass
class LLMJudgment:
    verdict: bool
    hype_score: float  # 0..1
    tags: List[str]
    title: str
    rationale: str


@dataclass
class HighlightEvent:
    event_id: str
    game_id: str
    league: League
    occurred_utc: datetime
    reasons: List[HighlightReason]
    base_score: float
    llm: Optional[LLMJudgment]
    combined_score: float
    tags: List[str]
    title: str
    clip_start_utc: datetime
    clip_end_utc: datetime
    storage_uri: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None
