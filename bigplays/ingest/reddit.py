from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generator, Optional


@dataclass
class RedditPost:
    created_utc: datetime
    title: str
    body: str
    score: int
    url: str


def stream_reddit(enabled: bool = False) -> Generator[RedditPost, None, None]:
    if not enabled:
        return
        yield  # pragma: no cover
    # Placeholder stub. You can implement with PRAW or Reddit API v2 here.
    # Yield new posts/comments with timestamps and scores.
    now = datetime.now(timezone.utc)
    yield RedditPost(created_utc=now, title="Demo", body="Big dunk!", score=42, url="https://reddit.com/demo")

