from datetime import datetime, timedelta, timezone

from bigplays.utils.time_utils import clamp_time


def test_clamp_time():
    t = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    s, e = clamp_time(t, 10, 5)
    assert s == t - timedelta(seconds=10)
    assert e == t + timedelta(seconds=5)

