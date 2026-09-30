"""Inspect configured live MLB sources and sample timestamped broadcast segments.

Run after starting the private resolver. Does not start the clipping agent, change
settings, or publish library clips. Reports omit signed stream URLs and secrets.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from bigplays.config import settings
from bigplays.ingest.live_streams import discover
from bigplays.media.ffmpeg_utils import ffmpeg_bin
from bigplays.media.scoreboard import frame_clock, run
from bigplays.ingest.plays import locate_play, mlb_pitches, parse_plays, read_scorebug, team_aliases
from bigplays.media.timeline import TimelineArchive, align_mlb_play, atomic_json, iso, select_window

SUMMARY_URL = 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary'


def contains(window, moment):
    return window is not None and window['start'] <= moment <= window['end']


async def alignment_report(directories, output, cut=False):
    """Align every ESPN pitch/play against saved footage and check hand-labelled truths.

    Each directory needs index.json + TS segments, raw-ocr.json (production sampling)
    and espn-summary.json. Optional ground-truth.json: [{'play_id', 'release_utc',
    'play_over_utc', 'note'}] read off the frames by a human.
    """
    games = []
    for directory in directories:
        summary = json.loads((directory / 'espn-summary.json').read_text())
        aliases = team_aliases(summary)
        frames = json.loads((directory / 'raw-ocr.json').read_text())
        observations = [r | {'time': f['time']} for f in frames
                        if (r := read_scorebug(f['rows'], aliases, league='mlb'))]
        segments = json.loads((directory / 'index.json').read_text())
        span = (segments[0]['start'], segments[-1]['start'] + segments[-1]['duration'])
        truths = {t['play_id']: t for t in json.loads((directory / 'ground-truth.json').read_text())} \
            if (directory / 'ground-truth.json').exists() else {}
        pitches, results = [], []
        for pitch in mlb_pitches(summary):
            outcome = align_mlb_play(pitch, observations)
            if outcome['window'] is None and 'not on screen' in outcome['reason'] and pitch['play_id'] not in truths:
                continue   # pitch happened outside the recorded span
            pitches.append(_entry(pitch, outcome, truths.get(pitch['play_id'])))
        for play in parse_plays(summary, 'mlb'):
            window = locate_play(play, observations, league='mlb')
            if window is None and not (span[0] - 120 <= play['occurred'] <= span[1] + 120):
                continue
            entry = _entry(play, align_mlb_play(play, observations), truths.get(play.get('pitch_play_id')))
            if window and cut:
                archive = TimelineArchive(directory, 'http://127.0.0.1:3000')
                try:
                    select_window(archive.segments, window['start'], window['end'])
                    out = directory / 'clips' / f"{play['play_id']}.mp4"
                    await archive.cut(window['start'], window['end'], out, {'play': play['text']})
                    entry['clip'] = str(out)
                except ValueError as error:
                    entry['clip_error'] = str(error)
            results.append(entry)
        legible = sum(bool(o.get('batter_id') and o.get('pitcher_id') and o.get('balls') is not None) for o in observations)
        games.append({'directory': str(directory), 'buffer_start': iso(span[0]), 'buffer_end': iso(span[1]),
                      'ocr_frames': len(frames), 'scorebug_frames': len(observations), 'full_signature_frames': legible,
                      'pitches_aligned': sum(p['aligned'] for p in pitches), 'pitches_considered': len(pitches),
                      'ground_truth_checked': sum('truth' in p for p in pitches),
                      'ground_truth_contained': sum(bool(p.get('truth', {}).get('release_in_window')) and
                                                    p['truth'].get('play_over_in_window', True) is not False for p in pitches),
                      'pitches': pitches, 'plays': results})
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'games': games,
              'defaults': {k: v for k, v in vars(__import__('bigplays.media.timeline', fromlist=['x'])).items()
                           if k.startswith('MLB_')}}
    atomic_json(output, report)
    return report


def _entry(play, outcome, truth):
    window = outcome['window']
    entry = {'play_id': play['play_id'], 'text': play['text'], 'event_type': play.get('event_type'),
             'espn_wallclock': iso(play['occurred']), 'batter': play.get('batter'), 'pitcher': play.get('pitcher'),
             'signature': f"P:{(play.get('pitcher_pitch_count') or 0) - 1} {play.get('balls_before')}-{play.get('strikes_before')}",
             'aligned': window is not None, 'reason': outcome['reason']}
    if window:
        entry.update(clip_start=iso(window['start']), clip_end=iso(window['end']),
                     last_pre_frame=iso(window['last_pre_time']), first_post_frame=iso(window['time']),
                     duration=round(window['end'] - window['start'], 2),
                     video_minus_espn_seconds=round(window['offset_seconds'], 2))
    if truth:
        release = timestamp_of(truth['release_utc'])
        over = timestamp_of(truth['play_over_utc']) if truth.get('play_over_utc') else None
        entry['truth'] = truth | {'release_in_window': contains(window, release),
                                  'play_over_in_window': None if over is None else contains(window, over)}
        if window:
            entry['truth']['pre_roll_seconds'] = round(release - window['start'], 2)
            if over is not None:
                entry['truth']['post_roll_after_play_seconds'] = round(window['end'] - over, 2)
    return entry


def timestamp_of(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


async def resolve(client, game):
    response = await client.post(settings.resolver_base_url.rstrip('/') + '/api/stream',
                                 headers={'Authorization': 'Bearer ' + settings.resolver_api_key},
                                 json={'url': game['url']}, timeout=60)
    response.raise_for_status()
    return response.json()


async def record(client, archive, root, seconds, game):
    """Keep refreshing the live playlist so a continuous span (with pitches) is archived.

    Signed playlists expire; after repeated failures resolve the page again, as the
    capture agent does. Recording stops early (keeping what it has) if that fails too.
    """
    deadline = time.monotonic() + seconds
    failures = resolves = 0
    while time.monotonic() < deadline:
        try:
            await archive.refresh(client, root)
            failures = 0
        except Exception as error:
            failures += 1
            # Types only: exception strings can contain signed URLs.
            print(f'refresh failed: {type(error).__name__}', file=sys.stderr)
            if failures >= 3:
                try:
                    result = await resolve(client, game)
                except Exception as error:
                    result = {'ok': False, 'stage': type(error).__name__}
                resolves += 1
                print(f"re-resolved: ok={result.get('ok')} stage={result.get('stage')}", file=sys.stderr)
                if result.get('ok'):
                    root, archive.playlist, failures = result['proxiedUrl'], None, 0
                elif resolves >= 10:
                    return
                else:
                    await asyncio.sleep(10)
        await asyncio.sleep(3)


async def ocr_archive(archive, directory, per_segment):
    observations = []
    for segment in archive.segments:
        step = segment['duration'] / per_segment
        for index in range(per_segment):
            offset = round(step * (index + .5), 3)
            rows = await frame_clock(directory / segment['file'], offset, settings.scoreboard_ocr_bin)
            observations.append({'time': segment['start'] + offset, 'rows': rows})
    return observations


async def inspect_game(game, directory, seconds=0, per_segment=2):
    report = {'game_id': game['game_id'], 'name': game['name'], 'discovery': game['status']}
    async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
        started = time.monotonic()
        result = await resolve(client, game)
        report['resolve_seconds'] = round(time.monotonic() - started, 2)
        report['resolved'] = bool(result.get('ok'))
        if not result.get('ok'):
            report['failure_stage'] = result.get('stage', 'resolver')
            return report
        archive = TimelineArchive(directory, settings.resolver_base_url, retention_minutes=max(5, seconds / 60 + 2),
                                  max_bytes=max(128, int(seconds * 1.5)) * 1024**2)
        started = time.monotonic()
        await archive.refresh(client, result['proxiedUrl'])
        report['buffer_seconds'] = round(time.monotonic() - started, 2)
        if seconds:
            await record(client, archive, result['proxiedUrl'], seconds, game)
        report['segments'] = len(archive.segments)
        if not archive.segments:
            return report
        first, last = archive.segments[0], archive.segments[-1]
        report.update(buffer_start=iso(first['start']), buffer_end=iso(last['start'] + last['duration']),
                      seconds_buffered=round(last['start'] + last['duration'] - first['start'], 3),
                      broadcast_lag_seconds=round(time.time() - last['start'] - last['duration'], 3),
                      bytes_buffered=sum(s['bytes'] for s in archive.segments))
        if seconds:
            observations = await ocr_archive(archive, directory, per_segment)
        else:
            observations = []
            for segment in archive.segments[-3:]:
                for offset in (min(1, segment['duration'] / 3), max(1, segment['duration'] - 1)):
                    rows = await frame_clock(directory / segment['file'], offset, settings.scoreboard_ocr_bin)
                    observations.append({'time': segment['start'] + offset, 'rows': rows})
        atomic_json(directory / 'raw-ocr.json', observations)
        report['ocr_frames'] = len(observations)
        # Freeze the play-by-play that matches this footage for offline alignment checks.
        summary = await client.get(SUMMARY_URL, params={'event': game['game_id']})
        if summary.is_success:
            atomic_json(directory / 'espn-summary.json', summary.json())
        sample = directory / 'latest-frame.jpg'
        await run(ffmpeg_bin(), '-v', 'error', '-y', '-i', str(directory / last['file']),
                  '-ss', str(min(1, last['duration'] / 3)), '-frames:v', '1', str(sample))
        report['sample_frame'] = str(sample)
        # Decode the whole latest segment, not just its header or duration.
        await run(ffmpeg_bin(), '-v', 'error', '-i', str(directory / last['file']), '-f', 'null', '-')
        report['segment_decodes'] = True
        report['checked_at'] = datetime.now(timezone.utc).isoformat()
    return report


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-id')
    parser.add_argument('--output', type=Path, default=Path('data/validation/mlb'))
    parser.add_argument('--seconds', type=int, default=0,
                        help='keep recording this long (continuous span for pitch alignment)')
    parser.add_argument('--frames-per-segment', type=int, default=2)
    parser.add_argument('--align', type=Path, nargs='+', metavar='DIR',
                        help='offline: align ESPN plays against saved footage dirs, write alignment-report.json')
    parser.add_argument('--cut', action='store_true', help='with --align, encode each aligned play window')
    args = parser.parse_args()
    if args.align:
        report = await alignment_report(args.align, args.output / 'alignment-report.json', cut=args.cut)
        for game in report['games']:
            print(json.dumps({k: v for k, v in game.items() if k not in ('pitches', 'plays')}))
            for play in game['plays']:
                print(' ', play['aligned'], play['text'][:50], play.get('clip_start'), play.get('clip_end'), play['reason'])
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    discovery = await discover()
    games = [g for g in discovery['games'] if g['league'] == 'mlb' and g['status'] == 'matched'
             and (not args.game_id or g['game_id'] == args.game_id)]
    outcomes = await asyncio.gather(*(inspect_game(g, args.output / g['game_id'], args.seconds, args.frames_per_segment) for g in games), return_exceptions=True)
    results = []
    for game, outcome in zip(games, outcomes):
        if isinstance(outcome, BaseException):
            # Exception messages can contain signed URLs. Keep only their type/status.
            outcome = {'game_id': game['game_id'], 'name': game['name'], 'error': type(outcome).__name__}
        results.append(outcome)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'matched_games': len(games),
              'games': results, 'warnings': discovery['warnings'],
              'note': 'Broadcast lag measures playlist UTC timestamps; scorer-to-clip delay needs an actual aligned play cut.'}
    atomic_json(args.output / 'report.json', report)
    print(json.dumps(report, indent=2))
    return 0 if results and all(g.get('segment_decodes') for g in results) else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
