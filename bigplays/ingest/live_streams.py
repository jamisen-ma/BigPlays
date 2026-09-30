"""Match ESPN live games to PPV catalog entries without guessing ambiguous streams."""
import asyncio
import re
import time
from datetime import datetime, timezone

import httpx

from bigplays.ingest.espn import SCOREBOARDS
from bigplays.ingest.ppv_provider import API_BASE, PUBLIC_ORIGIN, valid_event_uri


def words(value):
    return ' ' + ' '.join(re.findall(r'[a-z0-9]+', str(value).lower())) + ' '


def catalog_entries(payload):
    if not isinstance(payload, dict):
        raise ValueError('Invalid PPV catalog')
    root = payload.get('data', payload.get('streams'))
    if isinstance(root, dict):
        root = root.get('streams', root.get('categories'))
    if not isinstance(root, list):
        raise ValueError('PPV catalog schema is unavailable or unsupported')
    entries = []
    for item in root:
        if isinstance(item, dict):
            entries.extend(item['streams'] if isinstance(item.get('streams'), list) else [item])
    return [x for x in entries if isinstance(x, dict)]


def match_games(scoreboards, entries, state='in'):
    catalog_available = entries is not None
    entries = entries or []
    games = []
    now = time.time()
    for league, payload in scoreboards.items():
        for event in payload.get('events', []):
            if event.get('status', {}).get('type', {}).get('state') != state:
                continue
            comps = event.get('competitions', [])
            teams = comps[0].get('competitors', []) if comps else []
            if len(teams) != 2:
                continue
            aliases = []
            for competitor in teams:
                team = competitor.get('team', {})
                # College mascots are shared by many schools; match school identities.
                keys = ('displayName', 'shortDisplayName', 'location', 'abbreviation') if league == 'ncaaf' else (
                    'displayName', 'shortDisplayName', 'name', 'abbreviation')
                aliases.append([words(team[k]) for k in keys if team.get(k)])
            matches = []
            for entry in entries:
                tag = words(entry.get('tag', '')).strip()
                if tag in {'nba', 'nfl', 'mlb', 'ncaaf', 'cfb', 'college football', 'ncaa football'}:
                    entry_league = 'ncaaf' if tag in {'ncaaf', 'cfb', 'college football', 'ncaa football'} else tag
                    if entry_league != league:
                        continue
                slug = entry.get('uri_name', entry.get('uri', entry.get('slug', '')))
                if not valid_event_uri(slug):
                    continue
                # Skip explicitly ended or not-yet-started listings when timestamps exist.
                try:
                    if entry.get('ends_at') and float(entry['ends_at']) < now:
                        continue
                    if state == 'in' and entry.get('starts_at') and float(entry['starts_at']) > now:
                        continue
                    if state == 'pre' and entry.get('starts_at') and event.get('date'):
                        kickoff = datetime.fromisoformat(event['date'].replace('Z', '+00:00')).timestamp()
                        if abs(float(entry['starts_at']) - kickoff) > 6 * 3600:
                            continue
                except (ValueError, TypeError):
                    continue
                title = words(str(entry.get('name', '')) + ' ' + str(entry.get('title', '')) + ' ' + slug)
                if all(any(alias in title for alias in choices) for choices in aliases):
                    matches.append({'name': entry.get('name', entry.get('title', slug)), 'url': PUBLIC_ORIGIN + '/live/' + slug})
            unique = {m['url']: m for m in matches}
            matches = list(unique.values())
            games.append({'game_id': str(event.get('id', '')), 'league': league, 'name': event.get('name', ''),
                          'starts_at': event.get('date'),
                          'status': ('catalog_unavailable' if not catalog_available else
                                     'matched' if len(matches) == 1 else 'ambiguous' if matches else 'unmatched'),
                          'url': matches[0]['url'] if len(matches) == 1 else None, 'candidates': matches})
    return games


def upcoming_games(scoreboards, entries=None):
    upcoming = []
    now = datetime.now(timezone.utc)
    for game in match_games(scoreboards, entries, state='pre'):
        try:
            start = datetime.fromisoformat(game['starts_at'].replace('Z', '+00:00'))
            if start.tzinfo is None or start < now:
                continue
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        upcoming.append({**game, 'starts_at': start.astimezone(timezone.utc).isoformat()})
    return sorted(upcoming, key=lambda g: g['starts_at'])


def scoreboard_payload(response):
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
        raise ValueError('Unsupported scoreboard response')
    for event in payload['events']:
        if not isinstance(event, dict):
            raise ValueError('Unsupported scoreboard event')
        status = event.get('status', {})
        competitions = event.get('competitions', [])
        if (not isinstance(status, dict) or not isinstance(status.get('type', {}), dict)
                or not isinstance(competitions, list)):
            raise ValueError('Unsupported scoreboard event')
        for competition in competitions:
            if not isinstance(competition, dict) or not isinstance(competition.get('competitors', []), list):
                raise ValueError('Unsupported scoreboard competition')
            for competitor in competition.get('competitors', []):
                if not isinstance(competitor, dict) or not isinstance(competitor.get('team', {}), dict):
                    raise ValueError('Unsupported scoreboard team')
    return payload


async def discover():
    sources = [(league.value, url) for league, url in SCOREBOARDS.items()] + [('ppv', API_BASE + '/streams')]
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        responses = await asyncio.gather(*(client.get(url) for _, url in sources), return_exceptions=True)
    scoreboards, warnings = {}, []
    entries = None
    for (source, _), response in zip(sources, responses):
        try:
            if isinstance(response, Exception):
                raise response
            if source == 'ppv':
                response.raise_for_status()
                entries = catalog_entries(response.json())
            else:
                scoreboards[source] = scoreboard_payload(response)
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            error = ('PPV stream catalog unavailable; automatic stream matching is temporarily unavailable.' if source == 'ppv'
                     else f'{source.upper()} scoreboard unavailable; live games for this league could not be checked.')
            warnings.append({'stage': source, 'error': error})
    result = {'ok': bool(scoreboards), 'scoreboards_ok': len(scoreboards) == len(SCOREBOARDS),
              'checked_at': datetime.now(timezone.utc).isoformat(),
              'games': match_games(scoreboards, entries), 'upcoming': upcoming_games(scoreboards, entries), 'warnings': warnings}
    if not scoreboards:
        result.update(stage='discovery', error='No sports scoreboards could be checked. Retrying automatically.')
    return result
