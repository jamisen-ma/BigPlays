"""Match ESPN live games to PPV catalog entries without guessing ambiguous streams."""
import asyncio
import re
import time

import httpx

from bigplays.ingest.espn import NBA_SCOREBOARD, NFL_SCOREBOARD


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


def match_games(scoreboards, entries):
    games = []
    now = time.time()
    for league, payload in scoreboards.items():
        for event in payload.get('events', []):
            if event.get('status', {}).get('type', {}).get('state') != 'in':
                continue
            comps = event.get('competitions', [])
            teams = comps[0].get('competitors', []) if comps else []
            if len(teams) != 2:
                continue
            aliases = []
            for competitor in teams:
                team = competitor.get('team', {})
                aliases.append([words(team[k]) for k in ('displayName', 'shortDisplayName', 'name', 'abbreviation') if team.get(k)])
            matches = []
            for entry in entries:
                slug = entry.get('uri_name', entry.get('uri', entry.get('slug', '')))
                if not isinstance(slug, str) or not re.fullmatch(r'(?:24/)?[A-Za-z0-9_-]+', slug):
                    continue
                # Skip explicitly ended or not-yet-started listings when timestamps exist.
                try:
                    if entry.get('ends_at') and float(entry['ends_at']) < now:
                        continue
                    if entry.get('starts_at') and float(entry['starts_at']) > now:
                        continue
                except (ValueError, TypeError):
                    continue
                title = words(str(entry.get('name', '')) + ' ' + str(entry.get('title', '')) + ' ' + slug)
                if all(any(alias in title for alias in choices) for choices in aliases):
                    matches.append({'name': entry.get('name', entry.get('title', slug)), 'url': 'https://ppv.to/live/' + slug})
            unique = {m['url']: m for m in matches}
            matches = list(unique.values())
            games.append({'game_id': str(event.get('id', '')), 'league': league, 'name': event.get('name', ''),
                          'status': 'matched' if len(matches) == 1 else 'ambiguous' if matches else 'unmatched',
                          'url': matches[0]['url'] if len(matches) == 1 else None, 'candidates': matches})
    return games


async def discover():
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        responses = await asyncio.gather(*(client.get(url) for url in [NBA_SCOREBOARD, NFL_SCOREBOARD, 'https://api.ppv.to/api/streams']))
    for response in responses:
        response.raise_for_status()
    nba, nfl, catalog = [response.json() for response in responses]
    return match_games({'nba': nba, 'nfl': nfl}, catalog_entries(catalog))
