from datetime import datetime, timezone

from bigplays.models import HighlightEvent, League


def test_highlight_event_init():
    now = datetime.now(timezone.utc)
    he = HighlightEvent(
        event_id="abc",
        game_id="gid",
        league=League.NBA,
        occurred_utc=now,
        reasons=[],
        base_score=0.5,
        llm=None,
        combined_score=0.6,
        tags=["dunk"],
        title="Big dunk",
        clip_start_utc=now,
        clip_end_utc=now,
    )
    assert he.title == "Big dunk"

