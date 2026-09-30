"""Import all official individual NFL Week 3 highlights into the durable catalog.

Public NFL video pages identify the game and media. ESPN supplies event timestamps;
ambiguous play matches remain explicitly unresolved instead of using publication time.
Run: python -m bigplays.ingest.week3_archive --workers 6
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests

from bigplays.config import settings
from bigplays.media.ffmpeg_utils import ffmpeg_bin, media_duration_seconds, run_ffmpeg
from bigplays.storage.catalog import catalog_for

DATASET = 'nfl-2026-week3'
CACHE = Path('data/nfl-week3')
_local = threading.local()


def is_individual(item):
    tags = item.get('tags', [])
    return (item.get('category') == 'NFL Game Highlights'
            and any('2026-REG-3' in t.get('title', '') for t in tags)
            and not re.search(r'\bbest plays\b|\bhighlights\s*\||\bpreview\b|^Top (?:plays|runs|catches|touchdowns)',
                              item['title'], re.I)
            and not any(t.get('slug') in ('player-highlights-vc', 'team-highlights-vc',
                                         'nfl-highlight-compilation-vc') for t in tags))


def discover(cache=CACHE):
    cache.mkdir(parents=True, exist_ok=True)
    pages = cache / 'pages'
    pages.mkdir(exist_ok=True)
    response = requests.get('https://www.nfl.com/sitemap/html/videos/2026/9', timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    links = []
    for row in soup.select('tr'):
        cells, anchor = row.select('td'), row.select_one('a')
        if anchor and cells and '2026-09-24' <= cells[0].get_text(strip=True) <= '2026-09-30':
            links.append((cells[0].get_text(strip=True), anchor['href']))

    def fetch(row):
        date, path = row
        target = pages / (path.split('/')[-1] + '.json')
        if target.exists():
            return json.loads(target.read_text())
        response = requests.get('https://www.nfl.com' + path, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        config = soup.select_one('script[id^="video-config-"]')
        if not config:
            return None
        item = json.loads(config.string)['playlist'][0]
        item.update(source_url='https://www.nfl.com' + path, published_date=date,
                    schema=[json.loads(t.string) for t in soup.select('script[type="application/ld+json"]') if t.string])
        target.write_text(json.dumps(item, indent=2))
        return item

    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(fetch, row): row for row in links}
        for job in as_completed(jobs):
            try:
                result = job.result()
                if result:
                    rows.append(result)
            except Exception as error:
                errors.append({'url': jobs[job][1], 'error': str(error)})
    catalog = [r for r in rows if is_individual(r)]
    (cache / 'nfl-single-plays.json').write_text(json.dumps(catalog, indent=2))
    (cache / 'discovery-report.json').write_text(json.dumps({'pages': len(links), 'individual_plays': len(catalog), 'errors': errors}, indent=2))
    return catalog


def normalize(value):
    return re.sub('[^a-z0-9]', '', value.lower())


def match_play(item, plays):
    description = BeautifulSoup(item.get('description', ''), 'html.parser').get_text(' ', strip=True)
    text = item['title'] + ' ' + description
    names = []
    for tag in item.get('tags', []):
        if 'personId' in tag:
            name = re.sub(r'\s+(?:Jr\.?|Sr\.?|III|II|IV)$', '', tag['title']).split()
            names.append(normalize(name[-1]))
    yards = set(int(n) for n in re.findall(r'(\d+)[ -]yards?\b(?![- ]line)', description, re.I))
    if not yards:
        yards = set(int(n) for n in re.findall(r'(\d+)[ -]yards?\b(?![- ]line)', item['title'], re.I))
    kind = ('interception' if re.search(r'intercept|pick.six|pick(?:s|ed)? off|picks off', text, re.I)
            else 'fumble' if re.search(r'fumble|strip.sack|punch.out', text, re.I)
            else 'touchdown' if re.search(r'\bTD\b|touchdown', text, re.I)
            else 'sack' if re.search(r'\bsack', text, re.I)
            else 'field goal' if re.search(r'field.goal', text, re.I) else None)
    quarter = next((i for i, name in enumerate(['first', 'second', 'third', 'fourth'], 1)
                    if re.search(name + r'[- ]quarter', text, re.I)), None)
    candidates = []
    for p in plays:
        if not p.get('wallclock'):
            continue
        normalized = normalize(p.get('text', ''))
        names_hit = sum(bool(name) and name in normalized for name in names)
        if not names_hit:
            continue
        pt = p.get('type', {}).get('text', '').lower()
        body = p.get('text', '').lower()
        kind_hit = (kind is None or kind in pt or
                    (kind == 'interception' and 'intercepted' in body) or
                    (kind == 'fumble' and 'fumble' in body))
        if not kind_hit:
            continue
        if quarter and p.get('period', {}).get('number') != quarter:
            continue
        yards_hit = not yards or abs(p.get('statYardage', -999)) in yards
        if yards and not yards_hit:
            continue
        if kind is None and not yards:
            continue
        score = names_hit * 5 + (4 if yards else 0) + (4 if kind else 0)
        candidates.append((score, p))
    candidates.sort(key=lambda row: row[0], reverse=True)
    if candidates and (len(candidates) == 1 or candidates[0][0] > candidates[1][0]):
        match = candidates[0][1]
        published = next((s.get('datePublished') for s in item.get('schema', [])
                          if isinstance(s, dict) and s.get('datePublished')), None)
        # A video cannot show a play that happened after it was published.
        if not published or datetime.fromisoformat(match['wallclock']) <= datetime.fromisoformat(published):
            return match, [p['id'] for _, p in candidates]
    return None, [p['id'] for _, p in candidates]


def game_sources(cache=CACHE):
    path = cache / 'nfl-week3-scoreboard.json'
    legacy = cache.parent / 'nfl-week3-scoreboard.json'
    if legacy.exists() and not path.exists():
        path.write_bytes(legacy.read_bytes())
    if not path.exists():
        r = requests.get('https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
                         params={'dates': 2026, 'seasontype': 2, 'week': 3, 'limit': 100}, timeout=30)
        r.raise_for_status()
        path.write_text(json.dumps(r.json()))
    board = json.loads(path.read_text())
    if board['season']['year'] != 2026 or board['week']['number'] != 3 or len(board['events']) != 16:
        raise ValueError('Unexpected season/week or incomplete scoreboard')
    games = {}
    for event in board['events']:
        path = cache / f'{event["id"]}.json'
        if not path.exists():
            r = requests.get('https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary',
                             params={'event': event['id']}, timeout=30)
            r.raise_for_status()
            path.write_text(json.dumps(r.json()))
        payload = json.loads(path.read_text())
        raw = {p['id']: p for d in payload['drives']['previous'] for p in d.get('plays', [])}
        games[event['name']] = (event, sorted(raw.values(), key=lambda p: int(p['sequenceNumber'])))
    return games


class QuietLogger:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


def download(item, clips_dir):
    import yt_dlp
    target = clips_dir / f'nfl_{item["id"]}.mp4'
    duration = media_duration_seconds(target) if target.exists() else None
    if not duration:
        # A separate downloader per worker reuses its public playback session safely.
        if not hasattr(_local, 'downloader'):
            _local.downloader = yt_dlp.YoutubeDL({
                'format': 'best[height<=720]/best', 'quiet': True, 'no_warnings': True,
                'logger': QuietLogger(), 'socket_timeout': 30, 'retries': 3,
                'fragment_retries': 3, 'concurrent_fragment_downloads': 2,
                'skip_unavailable_fragments': False,
                'ffmpeg_location': ffmpeg_bin(), 'noprogress': True,
                'outtmpl': str(clips_dir / 'nfl_%(id)s.%(ext)s'),
            })
        # process_ie_result keeps the stable NFL content ID as the output identity.
        extractor = _local.downloader.get_info_extractor('NFL')
        info = extractor._extract_video(item['mcpID'])
        info['id'] = item['id']
        info['webpage_url'] = item['source_url']
        _local.downloader.process_ie_result(info, download=True)
        duration = media_duration_seconds(target)
        if not duration:
            raise ValueError('Downloaded video could not be decoded')
    poster = target.with_suffix('.jpg')
    if not poster.exists():
        run_ffmpeg(['-ss', str(min(3, duration / 3)), '-i', str(target),
                    '-frames:v', '1', '-vf', 'scale=480:-2', '-update', '1', str(poster)])
    return target, poster, duration


def build_record(item, event, plays, match, target, poster, duration, candidates):
    teams = {c['homeAway']: c['team'] for c in event['competitions'][0]['competitors']}
    published = next((s.get('datePublished') for s in item.get('schema', []) if isinstance(s, dict) and s.get('datePublished')), None)
    now = datetime.now(timezone.utc).isoformat()
    description = BeautifulSoup(item.get('description', ''), 'html.parser').get_text(' ', strip=True)
    people = [t['title'] for t in item.get('tags', []) if 'personId' in t]
    record = {
        'event_id': f'nfl-{item["id"]}', 'game_id': event['id'], 'league': 'nfl',
        'season': 2026, 'week': 3, 'replay_dataset': DATASET, 'demo': True, 'imported': True,
        'occurred_utc': match['wallclock'] if match else None,
        'received_utc': now, 'published_utc': published,
        'timestamp_source': 'ESPN play-by-play wallclock' if match else None,
        'timestamp_status': 'matched' if match else 'unresolved',
        'source_play_id': match['id'] if match else None,
        'play_candidates': candidates,
        'title': item['title'], 'description': description,
        'player': ', '.join(people), 'tags': ['Week 3'] + people[:2],
        'away': teams['away']['abbreviation'], 'home': teams['home']['abbreviation'],
        'away_color': '#' + teams['away'].get('color', '666666'),
        'home_color': '#' + teams['home'].get('color', '666666'),
        'period': f'Q{match["period"]["number"]}' if match else '',
        'clock': match['clock']['displayValue'] if match else '',
        'away_score': match['awayScore'] if match else None,
        'home_score': match['homeScore'] if match else None,
        'date': datetime.fromisoformat(event['date']).astimezone(ZoneInfo('America/New_York')).date().isoformat(),
        'source': {'title': item['title'], 'channel': 'NFL', 'url': item['source_url'],
                   'video_id': item['id'], 'mcp_id': item['mcpID'],
                   'play_by_play_url': f'https://www.espn.com/nfl/playbyplay/_/gameId/{event["id"]}'},
        'media_kind': 'broadcast', 'file': target.name, 'poster': poster.name,
        'clip_duration': duration, 'video_start': 0, 'video_end': duration,
        'storage_uri': str(target), 'reasons': [], 'base_score': 0, 'combined_score': 0,
    }
    if match:
        scoring, turnover = bool(match.get('scoringPlay')), bool(match.get('isTurnover'))
        record['reasons'] = (['big_scoring_play'] if scoring else []) + (['turnover'] if turnover else [])
        record['base_score'] = record['combined_score'] = .8 if turnover else .7 if scoring else .5
    return record


def run(workers=6, refresh=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    source = CACHE / 'nfl-single-plays.json'
    if refresh or not source.exists():
        rows = discover()
    else:
        rows = [r for r in json.loads(source.read_text()) if is_individual(r)]
    # Deduplicate URLs/cached pages that point to the same official video.
    rows = list({r['id']: r for r in rows}.values())
    games = game_sources()
    clips_dir = settings.clips_dir
    clips_dir.mkdir(parents=True, exist_ok=True)
    catalog = catalog_for(clips_dir, settings.database_path)
    reviewed_path = Path(__file__).with_name('week3_matches.json')
    overrides = json.loads(reviewed_path.read_text()) if reviewed_path.exists() else {}
    overrides_path = CACHE / 'play-matches.json'
    if overrides_path.exists():
        overrides.update(json.loads(overrides_path.read_text()))
    failures, completed, unresolved = [], [], []

    def import_one(item):
        tag = next(t['title'] for t in item['tags'] if '2026-REG-3' in t.get('title', ''))
        name = tag.removesuffix(' (2026-REG-3)')
        event, plays = games[name]
        match, candidates = match_play(item, plays)
        if item['id'] in overrides:
            match = (next(p for p in plays if p['id'] == overrides[item['id']])
                     if overrides[item['id']] else None)
        target, poster, duration = download(item, clips_dir)
        record = build_record(item, event, plays, match, target, poster, duration, candidates)
        if item['id'] in overrides:
            record['timestamp_match_method'] = ('Reviewed NFL video and ESPN play-by-play' if match else
                'No independent event wallclock: ESPN bundles conversion/PAT with the preceding touchdown')
        sidecar = target.with_suffix('.json')
        # Keep initial import time stable across retries and application restarts.
        if sidecar.exists():
            old = json.loads(sidecar.read_text())
            record['received_utc'] = old.get('received_utc', record['received_utc'])
        temporary = sidecar.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(record, indent=2))
        temporary.replace(sidecar)
        catalog.upsert(record)
        return record

    print(f'Importing {len(rows)} individual highlights from official NFL Week 3 pages', flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(import_one, item): item for item in rows}
        for job in as_completed(jobs):
            item = jobs[job]
            try:
                record = job.result()
                completed.append(record)
                if not record['occurred_utc']:
                    unresolved.append({'id': item['id'], 'game_id': record['game_id'], 'title': item['title'], 'candidates': record['play_candidates']})
            except Exception as error:
                failures.append({'id': item['id'], 'url': item['source_url'], 'error': str(error)})
                print(f'FAILED {item["title"]}: {error}', flush=True)
            if (len(completed) + len(failures)) % 10 == 0:
                print(f'{len(completed)}/{len(rows)} downloaded, {len(failures)} failures, {len(unresolved)} timestamps to review', flush=True)
    report = {'discovered': len(rows), 'downloaded': len(completed), 'games': len({r['game_id'] for r in completed}),
              'timestamp_matched': len(completed) - len(unresolved), 'unresolved': unresolved, 'failures': failures,
              'source': 'https://www.nfl.com/sitemap/html/videos/2026/9'}
    discovery_path = CACHE / 'discovery-report.json'
    if discovery_path.exists():
        report['discovery'] = json.loads(discovery_path.read_text())
    (CACHE / 'import-report.json').write_text(json.dumps(report, indent=2))
    catalog.save_import(DATASET, report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('unresolved', 'failures')}) + f' failures={len(failures)}', flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    report = run(max(1, min(args.workers, 8)), args.refresh)
    raise SystemExit(1 if report['failures'] else 0)
