"""Gamecast data layer: ESPN scoreboard + summary -> contract Game / Linescore / Play dicts.

See docs/games-api-contract.md. This module is pure data: it never attaches clips.
Clip fields (play.clip, viral, viral_reason, clip_count, viral_count) are left
null/false/0 for the API layer (bigplays/server/games.py) to fill.

Public API:
    await fetch_scoreboard(league, date=None)  -> [Game]
    await fetch_game(league, game_id)          -> {"game", "linescore", "plays"}
Pure parsers (no network, used by tests): parse_scoreboard, parse_game.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports'
SPORT_PATHS = {'nfl': 'football/nfl', 'mlb': 'baseball/mlb'}
LOCAL_TZ = ZoneInfo('America/Los_Angeles')

SCOREBOARD_TTL = 10.0
SUMMARY_TTL_LIVE = 8.0
SUMMARY_TTL_IDLE = 600.0
HTTP_TIMEOUT = 10.0

# MLB events that frame an at-bat/inning; they are folded into the at-bat, never shown.
_MLB_FRAME = {'start-batterpitcher', 'end-batterpitcher', 'start-inning', 'end-inning'}


class GamecastError(RuntimeError):
    """ESPN could not be reached/parsed and there is no previously good value to serve."""


# --------------------------------------------------------------------------- utils

def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != '' else None
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _utc(value: str | None) -> str | None:
    """ESPN '2026-09-30T00:00Z' / '...:12Z' -> '2026-09-30T00:00:00Z'."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed.astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%SZ')


def today_local() -> str:
    return datetime.now(LOCAL_TZ).strftime('%Y%m%d')


def _check_league(league: str) -> str:
    league = (league or '').lower()
    if league not in SPORT_PATHS:
        raise ValueError(f'unsupported league {league!r}; expected one of {sorted(SPORT_PATHS)}')
    return league


def nfl_period_label(period: int | None) -> str | None:
    if not period or period < 1:
        return None
    if period <= 4:
        return f'Q{period}'
    return 'OT' if period == 5 else f'{period - 4}OT'


def _logo(team: dict) -> str | None:
    if team.get('logo'):
        return team['logo']
    logos = team.get('logos') or []
    for want in (lambda r: 'scoreboard' in r and 'dark' not in r, lambda r: 'default' in r):
        for logo in logos:
            if want(logo.get('rel') or []):
                return logo.get('href')
    return logos[0].get('href') if logos else None


def _record(competitor: dict) -> str | None:
    for record in competitor.get('records') or competitor.get('record') or []:
        if record.get('type') == 'total' or record.get('name') == 'overall':
            return record.get('summary') or record.get('displayValue')
    return None


def _team(competitor: dict, state: str) -> dict:
    team = competitor.get('team') or {}
    winner = competitor.get('winner')
    return {
        'id': str(team.get('id') or competitor.get('id') or '') or None,
        'abbr': team.get('abbreviation'),
        'name': team.get('displayName'),
        'short_name': team.get('shortDisplayName') or team.get('name'),
        'logo': _logo(team),
        'color': f"#{team['color']}" if team.get('color') else None,
        'alt_color': f"#{team['alternateColor']}" if team.get('alternateColor') else None,
        'score': _int(competitor.get('score')) if state != 'pre' else None,
        'record': _record(competitor),
        'winner': bool(winner) if isinstance(winner, bool) and state == 'post' else None,
    }


def _broadcast(comp: dict) -> str | None:
    if comp.get('broadcast'):
        return comp['broadcast']
    names = []
    for item in comp.get('broadcasts') or []:
        if item.get('names'):  # scoreboard shape
            names.extend(item['names'])
        media = (item.get('media') or {}).get('shortName')  # summary header shape
        if media and (item.get('type') or {}).get('shortName') in ('TV', 'Streaming', None):
            names.append(media)
    names = list(dict.fromkeys(n for n in names if n))
    return ', '.join(names) or None


def _sides(comp: dict) -> tuple[dict, dict] | None:
    competitors = comp.get('competitors') or []
    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
    return (away, home) if home and away else None


def _mlb_half(status: dict) -> str | None:
    """'Top' | 'Bottom' | 'Mid' | 'End' from ESPN status text (e.g. 'Bot 2nd', 'Middle 7th')."""
    text = (status.get('periodPrefix') or (status.get('type') or {}).get('shortDetail')
            or (status.get('type') or {}).get('detail') or '')
    first = text.strip().split(' ')[0].lower()
    return {'top': 'Top', 'bot': 'Bottom', 'bottom': 'Bottom', 'mid': 'Mid', 'middle': 'Mid',
            'end': 'End'}.get(first)


def _period_fields(league: str, status: dict, state: str) -> tuple[int | None, str | None, str | None]:
    period = _int(status.get('period'))
    if state == 'pre' or not period:
        return None, None, None
    if league == 'nfl':
        clock = status.get('displayClock') if state == 'in' else None
        return period, nfl_period_label(period), clock
    detail = (status.get('type') or {}).get('shortDetail') or ''
    half = _mlb_half(status) if state == 'in' else None
    # periodPrefix on a summary header is stale for scheduled games; prefer the detail text.
    if state == 'in' and detail:
        half = _mlb_half({'type': {'shortDetail': detail}}) or half
    return period, (f'{half} {period}' if half else None), None


# ---------------------------------------------------------------------- situation

def _mlb_situation(sit: dict | None, names: dict[str, str] | None = None) -> dict | None:
    if not sit:
        return None
    names = names or {}

    def person(key: str) -> str | None:
        entry = sit.get(key) or {}
        athlete = entry.get('athlete') or {}
        if athlete.get('displayName'):
            return athlete['displayName']
        pid = str(entry.get('playerId') or athlete.get('id') or '')
        return names.get(pid) if pid else None

    return {'balls': _int(sit.get('balls')), 'strikes': _int(sit.get('strikes')), 'outs': _int(sit.get('outs')),
            'on_first': bool(sit.get('onFirst')), 'on_second': bool(sit.get('onSecond')),
            'on_third': bool(sit.get('onThird')), 'batter': person('batter'), 'pitcher': person('pitcher')}


def _nfl_situation(sit: dict | None, abbr_by_id: dict[str, str]) -> dict | None:
    if not sit:
        return None
    down, distance = _int(sit.get('down')), _int(sit.get('distance'))
    possession = sit.get('possession')
    if isinstance(possession, dict):
        possession = possession.get('id')
    yard_line_text = sit.get('possessionText') or None
    text = sit.get('downDistanceText') or None
    if not text and down and down > 0 and distance is not None and yard_line_text:
        text = f"{_ordinal(down)} & {distance} at {yard_line_text}"
    is_red_zone = sit.get('isRedZone')
    if is_red_zone is None and _int(sit.get('yardsToEndzone')) is not None:
        is_red_zone = _int(sit.get('yardsToEndzone')) <= 20
    return {'down': down if down and down > 0 else None, 'distance': distance if down and down > 0 else None,
            'yard_line_text': yard_line_text,
            'possession': abbr_by_id.get(str(possession)) if possession is not None else None,
            'is_red_zone': bool(is_red_zone), 'down_distance_text': text}


def _ordinal(n: int) -> str:
    return {1: '1st', 2: '2nd', 3: '3rd'}.get(n, f'{n}th')


# ---------------------------------------------------------------------- game shape

def _game(league: str, event_id: str, comp: dict, status: dict, *, start: str | None,
          venue: str | None, situation: dict | None, last_play_text: str | None) -> dict | None:
    sides = _sides(comp)
    if not sides:
        return None
    state = ((status.get('type') or {}).get('state') or 'pre').lower()
    if state not in ('pre', 'in', 'post'):
        state = 'pre'
    period, period_label, clock = _period_fields(league, status, state)
    away, home = sides
    return {
        'game_id': str(event_id), 'league': league,
        'status': state, 'status_detail': (status.get('type') or {}).get('shortDetail')
        or (status.get('type') or {}).get('detail'),
        'start_utc': _utc(start), 'venue': venue, 'broadcast': _broadcast(comp),
        'period': period, 'period_label': period_label, 'clock': clock,
        'away': _team(away, state), 'home': _team(home, state),
        'situation': situation if state == 'in' else None,
        'last_play_text': last_play_text,
        'clip_count': 0, 'viral_count': 0,
    }


def parse_scoreboard(league: str, payload: dict) -> list[dict]:
    """ESPN scoreboard JSON -> [Game] (pure)."""
    league = _check_league(league)
    games = []
    for event in payload.get('events') or []:
        comp = (event.get('competitions') or [{}])[0]
        status = event.get('status') or comp.get('status') or {}
        sit = comp.get('situation') or None
        sides = _sides(comp)
        abbr_by_id = {str(c.get('team', {}).get('id')): c.get('team', {}).get('abbreviation')
                      for c in (sides or ())}
        situation = (_mlb_situation(sit) if league == 'mlb' else _nfl_situation(sit, abbr_by_id)) if sit else None
        last_play = ((sit or {}).get('lastPlay') or {}).get('text') or None
        if last_play and league == 'mlb' and re.match(r'(?i)pitch \d+\s*:', last_play):
            last_play = None  # pitch-level ticker text, not a play summary
        game = _game(league, event.get('id'), comp, status, start=event.get('date') or comp.get('date'),
                     venue=(comp.get('venue') or {}).get('fullName'), situation=situation,
                     last_play_text=last_play)
        if game:
            games.append(game)
    return games


def parse_linescore(league: str, comp: dict) -> dict:
    sides = _sides(comp)
    if not sides:
        return {'periods': [], 'totals': {'away': None, 'home': None}}
    away, home = sides

    def values(competitor: dict) -> dict[int, int | None]:
        result = {}
        for index, item in enumerate(competitor.get('linescores') or []):
            number = _int(item.get('period')) or index + 1
            result[number] = _int(item.get('value') if item.get('value') is not None else item.get('displayValue'))
        return result

    away_v, home_v = values(away), values(home)
    count = max([9 if league == 'mlb' else 4, *away_v, *home_v])
    periods = []
    for number in range(1, count + 1):
        label = str(number) if league == 'mlb' else (str(number) if number <= 4 else nfl_period_label(number))
        periods.append({'label': label, 'away': away_v.get(number), 'home': home_v.get(number)})

    state = ((comp.get('status') or {}).get('type') or {}).get('state')

    def totals(competitor: dict) -> dict:
        score = _int(competitor.get('score')) if state != 'pre' else None
        if league == 'nfl':
            return {'score': score}
        hits, errors = _int(competitor.get('hits')), _int(competitor.get('errors'))
        if hits is None and competitor.get('linescores') and state != 'pre':
            per = [_int(i.get('hits')) for i in competitor['linescores']]
            hits = sum(per) if all(h is not None for h in per) else None
        if errors is None and competitor.get('linescores') and state != 'pre':
            per = [_int(i.get('errors')) for i in competitor['linescores']]
            errors = sum(per) if all(e is not None for e in per) else None
        return {'R': score, 'H': hits, 'E': errors}

    return {'periods': periods, 'totals': {'away': totals(away), 'home': totals(home)}}


# ---------------------------------------------------------------------- MLB plays

def mlb_names(payload: dict) -> dict[str, str]:
    """ESPN athlete id -> display name from rosters, box score and 'X pitches to Y' text."""
    names: dict[str, str] = {}
    for side in payload.get('rosters') or []:
        for entry in side.get('roster') or []:
            athlete = entry.get('athlete') or {}
            if athlete.get('id') and athlete.get('displayName'):
                names[str(athlete['id'])] = athlete['displayName']
    for side in (payload.get('boxscore') or {}).get('players') or []:
        for group in side.get('statistics') or []:
            for entry in group.get('athletes') or []:
                athlete = entry.get('athlete') or {}
                if athlete.get('id') and athlete.get('displayName'):
                    names.setdefault(str(athlete['id']), athlete['displayName'])
    for item in payload.get('plays') or []:
        match = re.fullmatch(r'(.+?) pitches to (.+?)\.?', (item.get('text') or '').strip())
        if not match:
            continue
        roles = {p.get('type'): str((p.get('athlete') or {}).get('id')) for p in item.get('participants') or []}
        for role, name in (('pitcher', match[1]), ('batter', match[2])):
            if roles.get(role) and roles[role] != 'None':
                names.setdefault(roles[role], name)
    return names


def _kind(item: dict) -> str:
    return ((item.get('type') or {}).get('type') or '').lower()


def _is_pitch(item: dict) -> bool:
    return item.get('summaryType') == 'P' and item.get('atBatPitchNumber') is not None


def _mlb_type(result: dict, event: dict | None) -> str:
    text = (result.get('text') or '').lower()
    alt = result.get('alternativeType') or (event or {}).get('type') or result.get('type') or {}
    if alt.get('type') == 'lineup-change':
        return 'Substitution'
    if re.search(r'\bstruck out\b', text):
        return 'Strikeout'
    if re.search(r'\bwalked\b', text) and 'intentionally' in text:
        return 'Intentional Walk'
    if re.search(r'\bwalked\b', text):
        return 'Walk'
    if re.search(r'\bhomered\b', text):
        return 'Home Run'
    if 'triple play' in text:
        return 'Triple Play'
    if 'double play' in text:
        return 'Double Play'
    return alt.get('alternativeText') or alt.get('text') or 'Play'


def parse_mlb_plays(payload: dict, abbr_by_id: dict[str, str]) -> list[dict]:
    """Collapse ESPN's per-pitch MLB feed into one Play per completed at-bat / notable event.

    Rule: each ESPN 'play-result' item becomes one Play whose play_id is that
    result's id. If it resolves a pitch (alternativePlay is a pitch) it is the
    at-bat result: related_play_ids lists every ESPN id in the at-bat (framing
    events, pitches, the decisive pitch, the result) except ids claimed by other
    results in the same at-bat. Results that resolve a non-pitch event (pickoff,
    stolen base, wild pitch ...) or a lineup change are separate Plays carrying
    their own id plus the event id. Standalone non-pitch events with no result
    wrapper are emitted as-is. At-bats still in progress (no result yet) are not
    emitted; the live count is in game.situation.
    """
    raw = [p for p in payload.get('plays') or [] if p.get('id') is not None]
    by_id = {str(p['id']): p for p in raw}
    names = mlb_names(payload)
    wrapped = {str(p['alternativePlay']) for p in raw if _kind(p) == 'play-result' and p.get('alternativePlay')}

    # Per-pitcher cumulative pitch number (ESPN counts the pitch itself).
    pitcher_counts: dict[str, int] = {}
    pitch_number: dict[str, int] = {}
    seen = set()
    for item in raw:
        if not _is_pitch(item):
            continue
        pitcher = next((str(p['athlete']['id']) for p in item.get('participants') or []
                        if p.get('type') == 'pitcher' and (p.get('athlete') or {}).get('id')), None)
        key = (item.get('atBatId'), item.get('atBatPitchNumber'))
        if pitcher and key not in seen:
            seen.add(key)
            pitcher_counts[pitcher] = pitcher_counts.get(pitcher, 0) + 1
            pitch_number[str(item['id'])] = pitcher_counts[pitcher]

    # Ids per at-bat, and which ids each non-final result claims for itself.
    at_bat_ids: dict[str, list[str]] = {}
    for item in raw:
        if item.get('atBatId'):
            at_bat_ids.setdefault(str(item['atBatId']), []).append(str(item['id']))

    plays: list[dict] = []
    play_at_bat: list[str] = []
    order = {str(p['id']): i for i, p in enumerate(raw)}
    for index, item in enumerate(raw):
        pid = str(item['id'])
        kind = _kind(item)
        is_result = kind == 'play-result'
        if not is_result:
            # Keep only standalone notable events that ESPN did not wrap in a result.
            if pid in wrapped or _is_pitch(item) or kind in _MLB_FRAME or item.get('summaryType') in ('P', 'A', 'I'):
                continue
            if not (item.get('text') or '').strip():
                continue
        event = by_id.get(str(item.get('alternativePlay'))) if item.get('alternativePlay') else None
        alt_kind = ((item.get('alternativeType') or {}).get('type') or '').lower()
        at_bat = str(item.get('atBatId') or '')
        if is_result and event is not None and _is_pitch(event):
            # At-bat result: all ids of the at-bat except those owned by other results/events.
            others = set()
            for other_id in at_bat_ids.get(at_bat, []):
                other = by_id[other_id]
                if other_id != pid and _kind(other) == 'play-result':
                    others.add(other_id)
                    if other.get('alternativePlay'):
                        others.add(str(other['alternativePlay']))
                elif other_id != pid and not _is_pitch(other) and _kind(other) not in _MLB_FRAME:
                    others.add(other_id)
            related = [i for i in at_bat_ids.get(at_bat, []) if i not in others
                       and _kind(by_id[i]) not in ('start-inning', 'end-inning')]
            pitches = [by_id[i] for i in related if _is_pitch(by_id[i])]
            final_pitch = event
        else:
            related = [pid] + ([str(item['alternativePlay'])] if event is not None else [])
            pitches, final_pitch = [], None
        period = item.get('period') or {}
        inning = _int(period.get('number'))
        half = (period.get('type') or '').title()
        half = {'Bot': 'Bottom'}.get(half, half)
        roles = {p.get('type'): str((p.get('athlete') or {}).get('id'))
                 for p in (final_pitch or item).get('participants') or item.get('participants') or []}
        mlb = None
        if final_pitch is not None:
            count = final_pitch.get('pitchCount') or {}
            mlb = {
                'batter': names.get(roles.get('batter', '')),
                'pitcher': names.get(roles.get('pitcher', '')),
                'balls': _int(count.get('balls')), 'strikes': _int(count.get('strikes')),
                'outs': _int(item.get('outs')),
                'pitch_count': len(pitches) or _int(final_pitch.get('atBatPitchNumber')),
                'pitcher_pitch_count': pitch_number.get(str(final_pitch['id'])),
            }
        type_label = _mlb_type(item, event) if is_result else ((item.get('type') or {}).get('text') or 'Play')
        if alt_kind == 'lineup-change':
            type_label = 'Substitution'
        team_id = str((item.get('team') or {}).get('id') or '')
        plays.append({
            # ESPN's MLB sequenceNumber restarts every at-bat; use feed position instead.
            'play_id': pid, 'sequence': index + 1,
            'period': inning, 'period_label': f'{half} {inning}' if half in ('Top', 'Bottom') and inning else None,
            'clock': None, 'text': (item.get('text') or '').strip(), 'type': type_label,
            'scoring': bool(item.get('scoringPlay')), 'team_abbr': abbr_by_id.get(team_id),
            'away_score': _int(item.get('awayScore')), 'home_score': _int(item.get('homeScore')),
            'wallclock_utc': _utc(item.get('wallclock')),
            'is_key_play': bool(item.get('scoringPlay')),
            'mlb': mlb, 'related_play_ids': related,
            'clip': None, 'viral': False, 'viral_reason': None,
        })
        play_at_bat.append(at_bat)
    # An at-bat that ended without a pitch result (e.g. inning-ending pickoff) still owns its
    # pitches: hand leftovers to the last non-substitution play of that at-bat.
    covered = {i for play in plays for i in play['related_play_ids']}
    for at_bat, ids in at_bat_ids.items():
        owners = [p for p, ab in zip(plays, play_at_bat) if ab == at_bat and p['type'] != 'Substitution']
        leftovers = [i for i in ids if i not in covered and _kind(by_id[i]) not in ('start-inning', 'end-inning')]
        if owners and leftovers:
            owner = owners[-1]
            owner['related_play_ids'] = sorted(set(owner['related_play_ids']) | set(leftovers),
                                               key=lambda i: order[i])
    return plays


# ---------------------------------------------------------------------- NFL plays

def parse_nfl_plays(payload: dict, abbr_by_id: dict[str, str]) -> list[dict]:
    drives = payload.get('drives') or {}
    ordered: list[tuple[dict, dict | None]] = []
    for drive in (drives.get('previous') or []) + ([drives['current']] if drives.get('current') else []):
        for item in drive.get('plays') or []:
            ordered.append((item, drive))
    if not ordered:  # some feeds put plays at the top level
        ordered = [(item, None) for item in payload.get('plays') or []]
    key_ids = {str(p.get('id')) for p in payload.get('scoringPlays') or []}
    for name in ('keyPlays', 'keyEvents'):
        key_ids |= {str(p.get('id')) for p in payload.get(name) or [] if isinstance(p, dict)}
    scoring_team = {str(p.get('id')): str((p.get('team') or {}).get('id') or '') for p in payload.get('scoringPlays') or []}
    plays, seen = [], set()
    for item, drive in ordered:
        pid = str(item.get('id') or '')
        if not pid or pid in seen:
            continue
        seen.add(pid)
        period = _int((item.get('period') or {}).get('number'))
        start, end = item.get('start') or {}, item.get('end') or {}
        team_id = (str((item.get('team') or {}).get('id') or '') or scoring_team.get(pid)
                   or str((start.get('team') or {}).get('id') or '') or str(((drive or {}).get('team') or {}).get('id') or ''))
        plays.append({
            'play_id': pid, 'sequence': _int(item.get('sequenceNumber')),
            'period': period, 'period_label': nfl_period_label(period),
            'clock': (item.get('clock') or {}).get('displayValue') or None,
            'text': (item.get('text') or '').strip(), 'type': (item.get('type') or {}).get('text') or None,
            'scoring': bool(item.get('scoringPlay')), 'team_abbr': abbr_by_id.get(team_id),
            'away_score': _int(item.get('awayScore')), 'home_score': _int(item.get('homeScore')),
            'wallclock_utc': _utc(item.get('wallclock')),
            'is_key_play': pid in key_ids or bool(item.get('scoringPlay')) or bool(item.get('isTurnover')),
            'mlb': None,
            'nfl': {'down_distance_text': start.get('downDistanceText') or None,
                    'yards': _int(item.get('statYardage')), 'drive_id': str((drive or {}).get('id') or '') or None,
                    'end_down_distance_text': end.get('downDistanceText') or None},
            'clip': None, 'viral': False, 'viral_reason': None,
        })
    return plays


def _nfl_live_situation(payload: dict, plays: list[dict], abbr_by_id: dict[str, str]) -> dict | None:
    if payload.get('situation'):
        return _nfl_situation(payload['situation'], abbr_by_id)
    drives = payload.get('drives') or {}
    current = drives.get('current') or ((drives.get('previous') or [None])[-1])
    items = (current or {}).get('plays') or []
    if not items:
        return None
    end = items[-1].get('end') or {}
    return _nfl_situation({'down': end.get('down'), 'distance': end.get('distance'),
                           'possessionText': end.get('possessionText'),
                           'downDistanceText': end.get('downDistanceText'),
                           'yardsToEndzone': end.get('yardsToEndzone'),
                           'possession': (end.get('team') or {}).get('id')}, abbr_by_id)


# ---------------------------------------------------------------------- game detail

def parse_game(league: str, summary: dict, scoreboard_game: dict | None = None) -> dict:
    """ESPN summary JSON -> {"game", "linescore", "plays"} (pure).

    `scoreboard_game` (a Game from parse_scoreboard for the same id) is optional; when
    given, its situation / last_play_text fill gaps the summary leaves.
    """
    league = _check_league(league)
    header = summary.get('header') or {}
    comp = (header.get('competitions') or [{}])[0]
    status = comp.get('status') or {}
    sides = _sides(comp) or ()
    abbr_by_id = {str((c.get('team') or {}).get('id')): (c.get('team') or {}).get('abbreviation') for c in sides}
    if league == 'mlb':
        plays = parse_mlb_plays(summary, abbr_by_id)
        situation = _mlb_situation(summary.get('situation'), mlb_names(summary))
    else:
        plays = parse_nfl_plays(summary, abbr_by_id)
        situation = _nfl_live_situation(summary, plays, abbr_by_id)
    last_text = next((p['text'] for p in reversed(plays) if p['text']), None)
    game = _game(league, header.get('id') or comp.get('id'), comp, status, start=comp.get('date'),
                 venue=((summary.get('gameInfo') or {}).get('venue') or {}).get('fullName'),
                 situation=situation, last_play_text=last_text)
    if game is None:
        raise GamecastError(f'{league} summary for {header.get("id")} has no home/away competitors')
    linescore = parse_linescore(league, comp)
    if game['status'] == 'post' and game['period'] is None:
        # Summary headers omit status.period for finals; the linescore knows how many were played.
        played = [i + 1 for i, p in enumerate(linescore['periods']) if p['away'] is not None or p['home'] is not None]
        game['period'] = played[-1] if played else None
    if scoreboard_game:
        if game['status'] == 'in' and scoreboard_game.get('situation'):
            game['situation'] = scoreboard_game['situation']
        for key in ('venue', 'broadcast'):
            game[key] = game[key] or scoreboard_game.get(key)
        for side in ('away', 'home'):
            for key in ('logo', 'color', 'alt_color', 'record'):
                game[side][key] = game[side][key] or (scoreboard_game.get(side) or {}).get(key)
    return {'game': game, 'linescore': linescore, 'plays': plays}


# ---------------------------------------------------------------------- fetch + cache

async def _http_get_json(url: str, params: dict | None = None) -> dict:
    # Keep httpx's default User-Agent: ESPN answers 403 to some custom agents.
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


# Tests replace this with a fixture loader.
fetch_json: Callable[[str, dict | None], Awaitable[dict]] = _http_get_json


class _Cache:
    """TTL cache with single-flight fetches and stale-on-error fallback."""

    def __init__(self) -> None:
        self.values: dict[tuple, tuple[float, Any]] = {}  # key -> (expires_monotonic, value)
        self.inflight: dict[tuple, asyncio.Future] = {}

    def clear(self) -> None:
        self.values.clear()
        self.inflight.clear()

    async def get(self, key: tuple, load: Callable[[], Awaitable[Any]], ttl_for: Callable[[Any], float]) -> Any:
        cached = self.values.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        loop = asyncio.get_running_loop()
        flight_key = (id(loop), key)
        future = self.inflight.get(flight_key)
        if future is not None and future.get_loop() is loop:
            return await asyncio.shield(future)
        future = loop.create_future()
        self.inflight[flight_key] = future
        try:
            value = await load()
        except Exception as exc:  # noqa: BLE001 - upstream/parse failures fall back to stale data
            stale = self.values.get(key)
            if stale is not None:
                log.warning('gamecast %s failed (%s); serving stale value', key, exc)
                result: Any = stale[1]
                future.set_result(result)
                return result
            error = exc if isinstance(exc, GamecastError) else GamecastError(f'gamecast fetch {key} failed: {exc!r}')
            future.set_exception(error)
            future.exception()  # mark retrieved so asyncio doesn't warn when nobody else waits
            raise error from exc
        else:
            self.values[key] = (time.monotonic() + ttl_for(value), value)
            future.set_result(value)
            return value
        finally:
            self.inflight.pop(flight_key, None)


_cache = _Cache()


def clear_cache() -> None:
    _cache.clear()


def _scoreboard_params(date: str | None, week: int | None, season: int | None) -> dict:
    if week is not None:
        params = {'week': str(week), 'seasontype': '2'}
        if season is not None:
            params['dates'] = str(season)
        return params
    return {'dates': date or today_local()}


async def fetch_scoreboard(league: str, date: str | None = None, *, week: int | None = None,
                           season: int | None = None) -> list[dict]:
    """Contract Game dicts for a league's slate. `date` is YYYYMMDD (default: today in LA).

    NFL callers may pass week (+ season) instead of date (regular season).
    """
    league = _check_league(league)
    if date is not None and not re.fullmatch(r'\d{8}', date):
        raise ValueError(f'date must be YYYYMMDD, got {date!r}')
    params = _scoreboard_params(date, week, season)
    url = f'{ESPN_BASE}/{SPORT_PATHS[league]}/scoreboard'

    async def load() -> list[dict]:
        return parse_scoreboard(league, await fetch_json(url, params))

    key = ('scoreboard', league, tuple(sorted(params.items())))
    return await _cache.get(key, load, lambda _: SCOREBOARD_TTL)


def _cached_scoreboard_game(league: str, game_id: str) -> dict | None:
    now = time.monotonic()
    for key, (expires, games) in _cache.values.items():
        if key[0] == 'scoreboard' and key[1] == league and expires > now:
            for game in games:
                if game['game_id'] == game_id:
                    return game
    return None


async def fetch_game(league: str, game_id: str) -> dict:
    """{"game": Game, "linescore": Linescore, "plays": [Play]} with plays oldest first."""
    league = _check_league(league)
    game_id = str(game_id)
    if not game_id.isdigit():
        raise ValueError(f'game_id must be numeric, got {game_id!r}')
    url = f'{ESPN_BASE}/{SPORT_PATHS[league]}/summary'

    async def load() -> dict:
        summary = await fetch_json(url, {'event': game_id})
        return parse_game(league, summary)

    def ttl(value: dict) -> float:
        return SUMMARY_TTL_LIVE if value['game']['status'] == 'in' else SUMMARY_TTL_IDLE

    result = await _cache.get(('summary', league, game_id), load, ttl)
    # Scoreboard situation carries player names and is at most ~10 s old; prefer it if cached.
    board = _cached_scoreboard_game(league, game_id)
    if board and result['game']['status'] == 'in' and board.get('situation'):
        result = dict(result, game=dict(result['game'], situation=board['situation']))
    return result
