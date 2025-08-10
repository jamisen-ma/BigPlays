from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Generator

from bigplays.models import SocialSignal


def mock_social_bursts(seed: int | None = None) -> Generator[SocialSignal, None, None]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    for i in range(1000000):
        now += timedelta(seconds=rng.randint(5, 25))
        score = max(0.0, min(1.0, rng.random() ** 0.7))
        text = rng.choice(
            [
                "HUGE DUNK!",
                "What a pick six",
                "Clutch three",
                "Game winner?",
                "Insane block",
                "Massive hit",
            ]
        )
        yield SocialSignal(platform="mock", occurred_utc=now, text=text, score=score, metadata={})

