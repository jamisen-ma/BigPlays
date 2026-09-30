"""Save official MLB highlights with original play times in the durable library.

python -m bigplays.ingest.mlb_archive --date 2026-09-29
The same import_date function can poll throughout a game; source IDs are stable.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import unicodedata
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from bigplays.config import settings
from bigplays.media.ffmpeg_utils import media_duration_seconds, run_ffmpeg
from bigplays.storage.catalog import catalog_for

API = 'https://statsapi.mlb.com'
CACHE = Path('data/mlb')
_import_lock = threading.Lock()


def _json(url, params=None):
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def _save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def fetch_schedule(date_string):
    date.fromisoformat(date_string)
    return _json(API + '/api/v1/schedule', {'sportId': 1, 'date': date_string, 'hydrate': 'team,linescore'})


def fetch_game(game_pk):
    return (_json(f'{API}/api/v1/game/{game_pk}/content'),
            _json(f'{API}/api/v1.1/game/{game_pk}/feed/live'))


def normalize_game(game, feed=None):
    feed = feed or {}
    linescore = feed.get('liveData', {}).get('linescore', game.get('linescore', {}))
    teams = feed.get('gameData', {}).get('teams', {})
    status = feed.get('gameData', {}).get('status', game.get('status', {}))
    inning = linescore.get('currentInning')
    half = (linescore.get('inningHalf') or '').lower()
    normalized = {
        'game_id': str(game['gamePk']), 'league': 'mlb',
        'date': game.get('officialDate'), 'start_time': game.get('gameDate'), 'starts_at': game.get('gameDate'),
        'status': {'Final': 'post', 'Live': 'in'}.get(status.get('abstractGameState'), 'pre'),
        'status_text': status.get('detailedState', ''), 'status_detail': status.get('detailedState', ''), 'game_type': game.get('gameType'),
        'series_description': game.get('seriesDescription', ''),
        'season': int(game.get('season', 0)), 'inning': inning, 'inning_half': half,
        'period': f'{half.title()} {inning}' if half and inning else '', 'clock': '',
        'balls': linescore.get('balls'), 'strikes': linescore.get('strikes'), 'outs': linescore.get('outs'),
        'source_url': f'https://www.mlb.com/gameday/{game["gamePk"]}',
    }
    for side in ('away', 'home'):
        team = teams.get(side) or game['teams'][side]['team']
        normalized[side] = team.get('abbreviation') or team.get('teamCode', '').upper() or team['name']
        normalized[side + '_name'] = team['name']
        normalized[side + '_score'] = linescore.get('teams', {}).get(side, {}).get('runs', game['teams'][side].get('score', 0))
        normalized[side + '_color'] = '#214b81' if side == 'away' else '#ba303f'
    return normalized


def _normalize(text):
    return re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()).strip()


def is_individual(item):
    tags = {tag.get('value') for tag in item.get('keywordsAll', []) if tag.get('type') == 'taxonomy'}
    title = item.get('title') or item.get('headline', '')
    if tags & {'game-recap', 'condensed-game', 'interview', 'data-visualization'}:
        return False
    if re.search(r'lineup|national anthem|ceremonial|probable pitcher|bullpen|bench availability|\bdiscuss|\breact|\bpope\b|condensed game', title, re.I):
        return False
    if re.search(r'(?:strikes out|fans) (?:\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b', title, re.I):
        return False
    return bool(item.get('playbacks') and (item.get('guid') or 'in-game-highlight' in tags or
                re.search(r'home run|homer|triple|double|single|strikeout|fans Harper', title, re.I)))


def match_play(item, feed):
    """Prefer MLB's exact GUID; only accept unique descriptive fallback matches."""
    plays = feed.get('liveData', {}).get('plays', {}).get('allPlays', [])
    reviewed_path = Path(__file__).with_name('mlb_play_matches.json')
    if reviewed_path.exists():
        reviewed = json.loads(reviewed_path.read_text()).get(f'{feed.get("gamePk")}/{item["id"]}')
        if reviewed:
            matches = [(p, e) for p in plays for e in p.get('playEvents', [])
                       if e.get('playId') == reviewed['play_id']]
            if len(matches) == 1:
                method = ('MLB original reviewed pitch' if 'capture-review' in item['id']
                          else 'Reviewed MLB source footage')
                return (*matches[0], method)
    ids = {item.get('guid'), item.get('playId')}
    ids.update(t.get('value') for t in item.get('keywordsAll', []) if t.get('type') in ('play_id', 'playId'))
    ids.discard(None)
    direct = [(p, e) for p in plays for e in p.get('playEvents', []) if e.get('playId') in ids]
    if len(direct) == 1:
        return (*direct[0], 'MLB content GUID')
    if ids:
        # Do not override an explicit, currently unmatched source identifier.
        return None, None, None
    title = item.get('title') or item.get('headline', '')
    text = _normalize(title + ' ' + item.get('description', ''))
    kind = ('home_run' if re.search(r'home run|homer', text) else
            'triple' if 'triple' in text else 'double' if 'double' in text else
            'single' if 'single' in text else
            'strikeout' if re.search(r'strikeout|strikes out|\bfans\b|\bponch|\bponche', text) else None)
    if not kind:
        return None, None, None
    inning_match = re.search(r'\b(\d+)(?:st|nd|rd|th|ta) (?:inning|entrada)', text)
    inning = int(inning_match[1]) if inning_match else None
    candidates = []
    for play in plays:
        if play.get('result', {}).get('eventType') != kind:
            continue
        if inning and play.get('about', {}).get('inning') != inning:
            continue
        matchup = play.get('matchup', {})
        batter = _normalize(matchup.get('batter', {}).get('fullName', ''))
        pitcher = _normalize(matchup.get('pitcher', {}).get('fullName', ''))
        if not batter or batter not in text:
            continue
        if kind == 'strikeout' and pitcher and pitcher not in text:
            continue
        pitches = [e for e in play.get('playEvents', []) if e.get('isPitch') and e.get('playId')]
        if pitches:
            candidates.append((play, pitches[-1]))
    if len(candidates) == 1:
        return (*candidates[0], 'MLB unique batter/event description')
    return None, None, None


def playback_url(item):
    rows = []
    for source in item.get('playbacks', []):
        url = source.get('url', '')
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if parsed.scheme != 'https' or not (host == 'mlb.com' or host.endswith('.mlb.com') or host.endswith('.mlbstatic.com')):
            continue
        if parsed.path.endswith('.mp4'):
            rows.append((0 if source.get('name') == 'mp4Avc' else 1, url))
        elif parsed.path.endswith('.m3u8'):
            rows.append((2, url))
    if not rows:
        raise ValueError('No official public MLB playback available')
    return sorted(rows)[0][1]


def stable_id(game_pk, item):
    digest = hashlib.sha256(str(item['id']).encode()).hexdigest()[:20]
    return f'mlb-{game_pk}-{digest}'


def download(item, game_pk, clips_dir):
    target = clips_dir / (stable_id(game_pk, item) + '.mp4')
    duration = media_duration_seconds(target) if target.exists() else None
    if not duration:
        url = playback_url(item)
        temporary = target.with_suffix('.part.mp4')
        if urlparse(url).path.endswith('.mp4'):
            with requests.get(url, stream=True, timeout=(15, 90)) as response:
                response.raise_for_status()
                with temporary.open('wb') as output:
                    for chunk in response.iter_content(1024 * 1024):
                        output.write(chunk)
        else:
            run_ffmpeg(['-i', url, '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-movflags', '+faststart', str(temporary)])
        duration = media_duration_seconds(temporary)
        if not duration:
            raise ValueError('Downloaded MLB footage could not be decoded')
        temporary.replace(target)
    poster = target.with_suffix('.jpg')
    if not poster.exists():
        run_ffmpeg(['-ss', str(min(3, duration / 3)), '-i', str(target), '-frames:v', '1',
                    '-vf', 'scale=480:-2', '-update', '1', str(poster)])
    return target, poster, duration


def count_before(play, event, feed):
    if not play or not event:
        return {}
    previous = None
    for candidate in play.get('playEvents', []):
        if candidate is event or candidate.get('playId') == event.get('playId'):
            break
        if candidate.get('count'):
            previous = candidate['count']
    if previous:
        return previous
    outs = 0
    for candidate in feed.get('liveData', {}).get('plays', {}).get('allPlays', []):
        if candidate is play:
            break
        about = candidate.get('about', {})
        if (about.get('inning'), about.get('halfInning')) == (play.get('about', {}).get('inning'), play.get('about', {}).get('halfInning')):
            outs = candidate.get('count', {}).get('outs', outs)
    return {'balls': 0, 'strikes': 0, 'outs': outs}


def build_record(item, game, feed, target, poster, duration):
    play, event, method = match_play(item, feed)
    occurred = event.get('startTime') if event else None
    published = item.get('date')
    if occurred and published and datetime.fromisoformat(occurred) > datetime.fromisoformat(published):
        play, event, method, occurred = None, None, None, None
    about, result = (play.get('about', {}), play.get('result', {})) if play else ({}, {})
    count = count_before(play, event, feed)
    normalized = normalize_game(game, feed)
    people = list(dict.fromkeys(t.get('displayName') for t in item.get('keywordsAll', []) if t.get('type') == 'player_id' and t.get('displayName')))
    inning, half = about.get('inning'), about.get('halfInning')
    return {
        'event_id': stable_id(game['gamePk'], item), 'game_id': str(game['gamePk']),
        'league': 'mlb', 'season': normalized['season'], 'date': game['officialDate'],
        'game_type': game.get('gameType'), 'series_description': normalized['series_description'],
        'replay_dataset': 'mlb-' + game['officialDate'], 'demo': False, 'imported': True,
        'occurred_utc': occurred, 'received_utc': datetime.now(timezone.utc).isoformat(), 'published_utc': published,
        'timestamp_status': 'matched' if occurred else 'unresolved',
        'timestamp_source': 'MLB play event startTime' if occurred else None, 'timestamp_match_method': method,
        'source_play_id': event.get('playId') if event else None,
        'play_start_utc': about.get('startTime'), 'play_end_utc': about.get('endTime'),
        'title': item.get('title') or item.get('headline', ''), 'description': item.get('description', ''),
        'player': ', '.join(people), 'tags': ['MLB', normalized['series_description']] + people[:2],
        **{key: normalized[key] for key in ('away', 'home', 'away_color', 'home_color')},
        'away_score': result.get('awayScore'), 'home_score': result.get('homeScore'),
        'inning': inning, 'inning_half': half, 'period': f'{half.title()} {inning}' if half and inning else '', 'clock': '',
        'balls': count.get('balls'), 'strikes': count.get('strikes'), 'outs': count.get('outs'),
        'count_context': 'before pitch' if event else None,
        'source': {'channel': 'MLB', 'title': item.get('title') or item.get('headline', ''),
                   'url': 'https://www.mlb.com/video/' + item.get('slug', item['id']), 'video_id': item['id'],
                   'play_by_play_url': normalized['source_url']},
        'media_kind': 'broadcast', 'file': target.name, 'poster': poster.name,
        'clip_duration': duration, 'video_start': 0, 'video_end': duration, 'storage_uri': str(target),
        'reasons': ['big_scoring_play'] if about.get('isScoringPlay') else [],
        'base_score': .8 if result.get('eventType') == 'home_run' else .6,
        'combined_score': .8 if result.get('eventType') == 'home_run' else .6,
    }


def import_date(date_string, workers=4):
    """Fetch current schedule and highlights; persist only new/updated records."""
    date.fromisoformat(date_string)
    with _import_lock:
        return _import_date(date_string, max(1, min(workers, 6)))


def _import_date(date_string, workers):
    cache = CACHE / date_string
    cache.mkdir(parents=True, exist_ok=True)
    schedule = fetch_schedule(date_string)
    _save_json(cache / 'schedule.json', schedule)
    _save_json(CACHE / f'schedule-{date_string}.json', schedule)
    catalog = catalog_for(settings.clips_dir, settings.database_path)
    settings.clips_dir.mkdir(parents=True, exist_ok=True)
    existing = {r['event_id']: r for r in catalog.all('mlb-' + date_string)}
    jobs, games, failures, completed, added = [], [], [], [], []
    for day in schedule.get('dates', []):
        for game in day.get('games', []):
            try:
                content, feed = fetch_game(game['gamePk'])
                _save_json(cache / f'{game["gamePk"]}-content.json', content)
                _save_json(cache / f'{game["gamePk"]}-live.json', feed)
                games.append(normalize_game(game, feed))
                items = content.get('highlights', {}).get('highlights', {}).get('items', [])
                for item in {r['id']: r for r in items if is_individual(r)}.values():
                    jobs.append((item, game, feed))
            except Exception as error:
                games.append(normalize_game(game))
                failures.append({'game_id': str(game['gamePk']), 'error': str(error)})

    def save(args):
        item, game, feed = args
        event_id = stable_id(game['gamePk'], item)
        old = existing.get(event_id)
        if old and old.get('file') and (settings.clips_dir / old['file']).exists():
            target = settings.clips_dir / old['file']
            poster = target.with_suffix('.jpg')
            duration = old.get('clip_duration') or media_duration_seconds(target)
            if not poster.exists() or not duration:
                target, poster, duration = download(item, game['gamePk'], settings.clips_dir)
        else:
            target, poster, duration = download(item, game['gamePk'], settings.clips_dir)
        record = build_record(item, game, feed, target, poster, duration)
        if old:
            record['received_utc'] = old['received_utc']
        if record != old:
            _save_json(target.with_suffix('.json'), record)
            catalog.upsert(record)
        return record, old is None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(save, args): args for args in jobs}
        for future in as_completed(pending):
            item, game, _ = pending[future]
            try:
                record, is_new = future.result()
                completed.append(record)
                if is_new:
                    added.append(record['event_id'])
            except Exception as error:
                failures.append({'game_id': str(game['gamePk']), 'id': item['id'], 'error': str(error)})
    report = {
        'date': date_string, 'updated_utc': datetime.now(timezone.utc).isoformat(), 'games': games,
        'checked_at': datetime.now(timezone.utc).isoformat(), 'game_count': len(games),
        'discovered': len(jobs), 'downloaded': len(completed), 'new': len(added), 'new_ids': added,
        'timestamp_matched': sum(bool(r['occurred_utc']) for r in completed),
        'unresolved': [{'id': r['event_id'], 'title': r['title']} for r in completed if not r['occurred_utc']],
        'failures': failures, 'source': f'{API}/api/v1/schedule?sportId=1&date={date_string}',
    }
    _save_json(cache / 'import-report.json', report)
    catalog.save_import('mlb-' + date_string, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat())
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    report = import_date(args.date, args.workers)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if report['failures'] else 0)
