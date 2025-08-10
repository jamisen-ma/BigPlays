from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_espn_timestamp(value: str) -> datetime:
    # ESPN often returns ISO timestamps with Z
    # Example: 2024-01-01T20:00Z or 2024-01-01T20:00:00Z
    # Fallback to fromisoformat without Z
    value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


def clamp_time(window_center: datetime, pre_seconds: int, post_seconds: int) -> tuple[datetime, datetime]:
    start = window_center - timedelta(seconds=pre_seconds)
    end = window_center + timedelta(seconds=post_seconds)
    return start, end

