"""Verified replay catalogs. Source event time and video offsets are independent."""
from datetime import datetime
import json
from pathlib import Path

from bigplays.demo.plays import PLAYS, DemoPlay


def replay_plays(dataset: str = 'highlights', league: str = 'all') -> list[DemoPlay]:
    if dataset == 'highlights':
        plays = PLAYS
    elif dataset == 'nfl-2026-week3':
        path = Path(__file__).with_name('data') / 'nfl-2026-week3.json'
        plays = [DemoPlay(**row) for row in json.loads(path.read_text())]
        for play in plays:
            event_time = datetime.fromisoformat(play.occurred_utc or '')
            if (event_time.tzinfo is None or play.season != 2026 or play.week != 3
                    or not play.source_play_id or not play.play_by_play_url
                    or not play.video_url or play.video_end is None
                    or not 0 <= play.video_start < play.video_end):
                raise ValueError(f'Incomplete verified replay: {play.play_id}')
    else:
        raise ValueError(f'Unknown replay dataset: {dataset}')
    return [p for p in plays if league == 'all' or p.league == league]
