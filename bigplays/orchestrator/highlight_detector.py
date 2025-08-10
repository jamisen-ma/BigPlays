from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from bigplays.config import settings
from bigplays.llm.reasoner import judge_highlight
from bigplays.models import HighlightEvent, HighlightReason, League, LLMJudgment, ScoringEvent
from bigplays.utils.time_utils import clamp_time


def _base_score(prev_scores: Tuple[int, int], curr_scores: Tuple[int, int], time_left_hint: Optional[float]) -> Tuple[float, List[HighlightReason]]:
    # Heuristics: lead change, large swing, clutch time boost
    prev_away, prev_home = prev_scores
    away, home = curr_scores
    reasons: List[HighlightReason] = []
    score = 0.0

    if (prev_home - prev_away) * (home - away) < 0:
        score += 0.35
        reasons.append(HighlightReason.LEAD_CHANGE)

    swing = abs((home - away) - (prev_home - prev_away))
    if swing >= 3:  # 3+ point swing in one update
        score += 0.25
        reasons.append(HighlightReason.MOMENTUM_SWING)

    points_delta = (home + away) - (prev_home + prev_away)
    if points_delta >= 6:
        score += 0.3
        reasons.append(HighlightReason.BIG_SCORING_PLAY)
    elif points_delta >= 3:
        score += 0.15
        reasons.append(HighlightReason.BIG_SCORING_PLAY)

    if time_left_hint is not None and time_left_hint <= 120:  # last 2 minutes
        score += 0.2
        reasons.append(HighlightReason.CLUTCH_TIME)

    return min(score, 1.0), reasons


def _llm_boost(context: List[str]) -> Optional[LLMJudgment]:
    return judge_highlight(context)


def detect_highlight(
    scoring_event: ScoringEvent,
    prev_home_score: int,
    prev_away_score: int,
    social_burst_score: float | None = None,
    time_left_hint: Optional[float] = None,
) -> Optional[HighlightEvent]:
    base, reasons = _base_score(
        prev_scores=(prev_away_score, prev_home_score),
        curr_scores=(scoring_event.away_score, scoring_event.home_score),
        time_left_hint=time_left_hint,
    )

    if social_burst_score and social_burst_score > 0.7:
        base += 0.2
        reasons.append(HighlightReason.SOCIAL_SPIKE)

    context_texts = [
        f"Game {scoring_event.game_id} {scoring_event.league.value}",
        scoring_event.description,
        f"Score: {scoring_event.away_score}-{scoring_event.home_score}",
    ]

    llm = _llm_boost(context_texts) if settings.use_llm else None
    if llm and llm.verdict:
        reasons.append(HighlightReason.LLM_VIRAL)
        combined = min(1.0, base * 0.6 + llm.hype_score * 0.6)
        tags = llm.tags
        title = llm.title or scoring_event.description
    else:
        combined = base
        tags = []
        title = scoring_event.description

    # Threshold gate
    if combined < 0.5:
        return None

    event_id = hashlib.sha1(
        f"{scoring_event.game_id}-{scoring_event.occurred_utc.isoformat()}-{scoring_event.home_score}-{scoring_event.away_score}".encode()
    ).hexdigest()[:12]

    start, end = clamp_time(scoring_event.occurred_utc, settings.pre_roll_seconds, settings.post_roll_seconds)

    return HighlightEvent(
        event_id=event_id,
        game_id=scoring_event.game_id,
        league=scoring_event.league,
        occurred_utc=scoring_event.occurred_utc,
        reasons=reasons,
        base_score=base,
        llm=llm,
        combined_score=combined,
        tags=tags,
        title=title,
        clip_start_utc=start,
        clip_end_utc=end,
        storage_uri=None,
        metadata={"desc": scoring_event.description},
    )

