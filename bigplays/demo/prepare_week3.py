"""Cache and cut the reviewed Week 3 footage. Run with --verify-sources to recheck ESPN."""
import argparse
import json
from pathlib import Path

import requests

from bigplays.config import settings
from bigplays.demo.replay import replay_plays
from bigplays.media.ffmpeg_utils import media_duration_seconds, run_ffmpeg


def verify_sources(plays):
    for game_id in sorted({p.game_id for p in plays}):
        response = requests.get('https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary',
                                params={'event': game_id}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        header = payload['header']
        if (header['season']['year'], header['season']['type'], header['week']) != (2026, 2, 3):
            raise ValueError(f'{game_id} is not 2026 regular-season Week 3')
        raw = {p['id']: p for d in payload['drives']['previous'] for p in d.get('plays', [])}
        for play in (p for p in plays if p.game_id == game_id):
            source = raw[play.source_play_id]
            expected = (play.occurred_utc, play.period, play.clock, play.away_after, play.home_after)
            actual = (source['wallclock'], f'Q{source["period"]["number"]}',
                      source['clock']['displayValue'], source['awayScore'], source['homeScore'])
            if expected != actual:
                raise ValueError(f'Source changed for {play.play_id}: {actual} != {expected}')
    print(f'Verified source timestamps, quarters, clocks and scores for {len(plays)} plays.', flush=True)


def prepare(cache_dir: Path, out_dir: Path, verify: bool = False):
    plays = replay_plays('nfl-2026-week3', 'nfl')
    evidence = json.loads((Path(__file__).with_name('data') / 'nfl-2026-week3-evidence.json').read_text())
    video_ids = {e['play']['id']: e['video_id'] for e in evidence}
    if verify:
        verify_sources(plays)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for play in plays:
        source = cache_dir / f'{video_ids[play.source_play_id]}.mp4'
        if not source.exists():
            temporary = source.with_suffix('.part')
            with requests.get(play.video_url, timeout=(10, 60), stream=True) as response:
                response.raise_for_status()
                with temporary.open('wb') as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        stream.write(chunk)
            temporary.replace(source)
        duration = media_duration_seconds(source)
        if duration is None or duration + .05 < play.video_end:
            raise ValueError(f'Video is shorter than its reviewed window: {source}')
        target = out_dir / f'{play.play_id}.mp4'
        expected = play.video_end - play.video_start
        actual = media_duration_seconds(target) if target.exists() else None
        if actual is None or abs(actual - expected) > .1:
            temporary = target.with_suffix('.tmp.mp4')
            run_ffmpeg(['-ss', str(play.video_start), '-i', str(source), '-t', str(expected),
                        '-vf', 'scale=-2:720', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22',
                        '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(temporary)])
            actual = media_duration_seconds(temporary)
            if actual is None or abs(actual - expected) > .1:
                raise ValueError(f'Incorrect cut duration: {temporary}: {actual} vs {expected}')
            temporary.replace(target)
        poster = target.with_suffix('.jpg')
        if not poster.exists():
            run_ffmpeg(['-ss', str(min(3, expected / 2)), '-i', str(target),
                        '-frames:v', '1', '-update', '1', str(poster)])
        print(f'{play.source_play_id}: {play.period} {play.clock}, {play.occurred_utc}, '
              f'video {play.video_start}–{play.video_end}s ({actual:.2f}s)', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-sources', action='store_true')
    parser.add_argument('--cache-dir', type=Path, default=Path('data/nfl-week3/video'))
    parser.add_argument('--out-dir', type=Path, default=settings.demo_clips_dir)
    args = parser.parse_args()
    prepare(args.cache_dir, args.out_dir, args.verify_sources)
