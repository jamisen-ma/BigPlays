"""Use actual play-by-play timestamps, never scoreboard polling time."""
from __future__ import annotations

import re

from bigplays.media.scoreboard import read_mlb_scorebug
from bigplays.media.timeline import align_mlb_play, timestamp

SUMMARY_PATHS = {'ncaaf': 'football/college-football', 'nfl': 'football/nfl',
                 'nba': 'basketball/nba', 'mlb': 'baseball/mlb'}


def clock_seconds(value: str) -> int | None:
    match = re.fullmatch(r'(\d{1,2}):([0-5]\d)', value.strip())
    return int(match[1]) * 60 + int(match[2]) if match else None


def parse_plays(payload: dict, league: str) -> list[dict]:
    if league == 'mlb':
        return parse_mlb_plays(payload)
    raw = list(payload.get('plays', [])) + list(payload.get('scoringPlays', []))
    drives = payload.get('drives', {})
    for drive in drives.get('previous', []) + [drives.get('current') or {}]:
        raw.extend(drive.get('plays', []))
    plays = {}
    for item in raw:
        try:
            occurred = timestamp(item['wallclock'])
            play_id = str(item['id'])
            if not play_id.isdigit():
                continue
            clock = item.get('clock', {}).get('displayValue', '')
            seconds = clock_seconds(clock)
            period = int(item.get('period', {}).get('number', 0))
            if seconds is None or period < 1:
                continue
            text = item.get('text', '')
            points = item.get('scoreValue') or 0
            kind = item.get('type', {}).get('text', '').lower()
            interesting = (bool(item.get('scoringPlay')) and (league == 'nba' or points >= 3
                           or 'touchdown' in kind or 'field goal' in kind)) or bool(item.get('isTurnover'))
            interesting |= league != 'nba' and item.get('statYardage', 0) >= 20
            plays[play_id] = {'play_id': play_id, 'occurred': occurred, 'period': period, 'clock': clock,
                              'clock_seconds': seconds, 'text': text, 'interesting': interesting,
                              'home_score': item.get('homeScore'), 'away_score': item.get('awayScore')}
        except (KeyError, TypeError, ValueError):
            continue  # No UTC time: cannot safely align this play.
    return sorted(plays.values(), key=lambda p: (p['occurred'], p['play_id']))


def _athlete(entry: dict) -> tuple[str, str, str] | None:
    athlete = entry.get('athlete') or {}
    if not athlete.get('id') or not athlete.get('displayName'):
        return None
    name = athlete['displayName']
    return str(athlete['id']), name, athlete.get('lastName') or name.split()[-1]


def mlb_roster(payload: dict) -> list[dict]:
    """Every player ESPN knows for this game: lineups, box score (incl. relievers), matchups.

    Returns [{'id', 'name', 'last', 'team'}]; used to resolve play participants and to
    fuzzy-match OCR'd scorebug names. Team is the ESPN team id when known.
    """
    players: dict[str, dict] = {}

    def add(pid, name, last, team=None):
        current = players.setdefault(pid, {'id': pid, 'name': name, 'last': last, 'team': team})
        current['team'] = current['team'] or team

    for side in payload.get('rosters', []):
        team = str(side.get('team', {}).get('id') or '') or None
        for entry in side.get('roster', []):
            if found := _athlete(entry):
                add(*found, team)
    for side in payload.get('boxscore', {}).get('players', []):
        team = str(side.get('team', {}).get('id') or '') or None
        for group in side.get('statistics', []):
            for entry in group.get('athletes', []):
                if found := _athlete(entry):
                    add(*found, team)
    # A reliever who just entered may not be in the box score yet; the matchup text
    # "X pitches to Y" still names both participants.
    for item in payload.get('plays', []):
        match = re.fullmatch(r'(.+?) pitches to (.+?)\.?', (item.get('text') or '').strip())
        roles = {p.get('type'): str(p.get('athlete', {}).get('id')) for p in item.get('participants', [])}
        if match and roles.get('pitcher') and roles.get('batter'):
            for role, name in (('pitcher', match[1]), ('batter', match[2])):
                if roles[role] not in players:
                    add(roles[role], name, name.split()[-1])
    return list(players.values())


def pitcher_pitch_counts(payload: dict) -> dict[str, dict]:
    """Cumulative per-pitcher pitch number for every pitch event, in game order.

    ESPN's pitch number includes the pitch itself (the broadcast "P:" shows completed
    pitches, so pitch N is on screen as P:N-1 before delivery). Returns
    {pitch_play_id: {'pitcher_id', 'number'}} plus '_totals' {pitcher_id: count}.
    """
    counts: dict[str, int] = {}
    seen = set()
    result: dict[str, dict] = {}
    for item in payload.get('plays', []):
        number = item.get('atBatPitchNumber')
        if item.get('summaryType') != 'P' or number is None:
            continue
        pitcher = next((str(p['athlete']['id']) for p in item.get('participants', [])
                        if p.get('type') == 'pitcher' and p.get('athlete', {}).get('id')), None)
        key = (item.get('atBatId'), number)
        if not pitcher or key in seen:
            continue
        seen.add(key)
        counts[pitcher] = counts.get(pitcher, 0) + 1
        result[str(item.get('id'))] = {'pitcher_id': pitcher, 'number': counts[pitcher]}
    result['_totals'] = counts
    return result


def _box_pitch_counts(payload: dict) -> dict[str, int]:
    totals = {}
    for side in payload.get('boxscore', {}).get('players', []):
        for group in side.get('statistics', []):
            if group.get('type') != 'pitching':
                continue
            keys = group.get('keys') or group.get('names') or group.get('labels') or []
            index = next((i for i, k in enumerate(keys) if str(k).lower() in ('pc', 'pitches', 'pitchcount')), None)
            if index is None:
                continue
            for entry in group.get('athletes', []):
                try:
                    totals[str(entry['athlete']['id'])] = int(entry['stats'][index])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
    return totals


def _mlb_context(payload: dict) -> dict[str, dict]:
    """Pre-play state for each play id: outs/score before it and the pitch that ended it."""
    raw = payload.get('plays', [])
    by_id = {str(p.get('id')): p for p in raw}
    context = {}
    outs, home, away, half = 0, 0, 0, None
    last_pitch_by_at_bat = {}
    for item in raw:
        period = item.get('period', {})
        current_half = (period.get('number'), period.get('type'))
        kind = item.get('type', {}).get('type', '')
        if kind == 'start-inning' or current_half != half:
            outs, half = 0, current_half
        pid = str(item.get('id'))
        is_pitch = item.get('summaryType') == 'P' and item.get('atBatPitchNumber') is not None
        previous = by_id.get(last_pitch_by_at_bat.get(item.get('atBatId')), {}) if is_pitch else {}
        # The count on screen before pitch k is pitch k-1's result. ESPN's own pre-pitch
        # count can be corrupted by replay reviews (e.g. "1-3" after an overturned call).
        count = previous.get('resultCount') if previous else ({'balls': 0, 'strikes': 0} if is_pitch else None)
        count = count or item.get('pitchCount') or {}
        balls, strikes = count.get('balls'), count.get('strikes')
        valid = isinstance(balls, int) and isinstance(strikes, int) and 0 <= balls <= 3 and 0 <= strikes <= 2
        if is_pitch:
            last_pitch_by_at_bat[item.get('atBatId')] = pid
        context[pid] = {'outs_before': outs, 'home_score_before': home, 'away_score_before': away,
                        'balls_before': balls if valid else None, 'strikes_before': strikes if valid else None,
                        'last_pitch_id': last_pitch_by_at_bat.get(item.get('atBatId'))}
        # ESPN stamps pitches and mid-at-bat events with the plate appearance's eventual
        # outs; only a completed plate appearance moves the pre-pitch state forward.
        if kind == 'end-batterpitcher' and isinstance(item.get('outs'), int):
            outs = item['outs']
        if isinstance(item.get('homeScore'), int):
            home = item['homeScore']
        if isinstance(item.get('awayScore'), int):
            away = item['awayScore']
    return context


def _signature_builder(payload: dict):
    """-> sign(pitch_id, item): the scorebug signature of the pitch that decided `item`."""
    raw = list(payload.get('plays', []))
    by_id = {str(p.get('id')): p for p in raw}
    roster = {p['id']: p for p in mlb_roster(payload)}
    pitch_numbers = pitcher_pitch_counts(payload)
    totals = pitch_numbers.pop('_totals')
    box_totals = _box_pitch_counts(payload)
    context = _mlb_context(payload)
    current_pitcher = next((v['pitcher_id'] for k, v in reversed(list(pitch_numbers.items()))), None)
    # Each pitcher's pitch sequence, used on screen to learn a constant "P:" offset when
    # ESPN dropped or merged a pitch that the broadcast counted.
    sequences: dict[str, list[dict]] = {}
    for pid, info in pitch_numbers.items():
        batter_id = next((str(x['athlete']['id']) for x in by_id.get(pid, {}).get('participants', [])
                          if x.get('type') == 'batter' and x.get('athlete', {}).get('id')), None)
        state = context.get(pid, {})
        sequences.setdefault(info['pitcher_id'], []).append(
            {'n': info['number'], 'batter_id': batter_id,
             'balls': state.get('balls_before'), 'strikes': state.get('strikes_before')})

    def sign(pitch_id: str | None, item: dict) -> dict:
        pitch_info = pitch_numbers.get(pitch_id or '', {})
        pitch_state = context.get(pitch_id or '', context.get(str(item.get('id')), {}))
        roles = {p.get('type'): str(p.get('athlete', {}).get('id')) for p in
                 (by_id.get(pitch_id or '', {}).get('participants') or item.get('participants') or [])}
        batter, pitcher = roster.get(roles.get('batter')), roster.get(roles.get('pitcher'))
        pitcher_id = pitch_info.get('pitcher_id')
        # Our running count must equal ESPN's box score total, else a pitch is missing.
        # Live, ESPN's box score runs a pitch or two ahead of play-by-play for the pitcher
        # still on the mound (the on-screen signature then verifies the number itself).
        ahead = box_totals.get(pitcher_id, 0) - totals.get(pitcher_id, 0)
        consistent = bool(pitcher_id) and (pitcher_id not in box_totals or ahead == 0
                                           or (pitcher_id == current_pitcher and 0 < ahead <= 2))
        return {
            'pitch_play_id': pitch_id if pitch_info else None,
            'batter_id': batter and batter['id'], 'batter': batter and batter['name'],
            'pitcher_id': pitcher and pitcher['id'], 'pitcher': pitcher and pitcher['name'],
            'pitcher_pitch_count': pitch_info.get('number'),
            'pitch_count_consistent': consistent,
            'balls_before': pitch_state.get('balls_before'),
            'strikes_before': pitch_state.get('strikes_before'),
            'outs_before': pitch_state.get('outs_before'),
            'home_score_before': pitch_state.get('home_score_before'),
            'away_score_before': pitch_state.get('away_score_before'),
            'recent_pitches': [q for q in sequences.get(pitcher_id, [])
                               if q['n'] < (pitch_info.get('number') or 0)][-12:],
        }
    return sign, context


def mlb_pitches(payload: dict) -> list[dict]:
    """Every pitch with its scorebug signature (for validation and pitch-level clips)."""
    sign, _ = _signature_builder(payload)
    result = []
    for item in payload.get('plays', []):
        if item.get('summaryType') != 'P' or item.get('atBatPitchNumber') is None:
            continue
        try:
            occurred = timestamp(item['wallclock'])
        except (KeyError, TypeError, ValueError):
            continue
        period = item.get('period', {})
        result.append({'play_id': str(item['id']), 'occurred': occurred, 'text': item.get('text', ''),
                       'inning': period.get('number'), 'inning_half': period.get('type'),
                       'event_type': item.get('type', {}).get('type', ''), 'result_count': item.get('resultCount')}
                      | sign(str(item['id']), item))
    return result


def parse_mlb_plays(payload: dict) -> list[dict]:
    """Read completed baseball events, preserving source wallclock and inning/count.

    ESPN emits both the final pitch and a descriptive Play Result for a plate
    appearance. Prefer the result so one home run becomes one candidate, and
    exclude roster changes and individual balls/strikes from highlight scoring.
    """
    raw = list(payload.get('plays', [])) + list(payload.get('scoringPlays', []))
    by_id = {str(p.get('id')): p for p in raw}
    result_pitch_ids = {str(p['alternativePlay']) for p in raw if p.get('alternativePlay')}
    sign, context = _signature_builder(payload)
    plays = {}
    for item in raw:
        try:
            play_id = str(item['id'])
            if not play_id.isdigit() or play_id in result_pitch_ids:
                continue
            primary = item.get('type', {})
            primary_kind = primary.get('type', primary.get('text', '')).lower().replace(' ', '-')
            kind_info = item.get('alternativeType') or primary
            kind = kind_info.get('type', kind_info.get('text', '')).lower().replace(' ', '-')
            if primary_kind != 'play-result' and item.get('summaryType') in ('P', 'A', 'I', 'C'):
                continue
            if kind in ('lineup-change', 'start-inning', 'end-inning', 'start-batterpitcher', 'end-batterpitcher'):
                continue
            # Results are complete events; bare pitches without a result are still
            # in progress and cannot safely be promoted from "Strike Swinging".
            if primary_kind != 'play-result' and kind in ('ball', 'strike-swinging', 'strike-looking', 'foul-ball'):
                continue
            occurred = timestamp(item['wallclock'])
            inning = int(item.get('period', {}).get('number', 0))
            half = item.get('period', {}).get('type', '').title()
            if inning < 1 or half not in ('Top', 'Bottom'):
                continue
            text = item.get('text', '')
            description = text.lower()
            state = context.get(play_id, {})
            pitch_id = str(item.get('alternativePlay') or '') or (
                play_id if item.get('summaryType') == 'P' else state.get('last_pitch_id'))
            final_pitch = by_id.get(str(item.get('alternativePlay')), {})
            count = final_pitch.get('pitchCount') or item.get('pitchCount') or {}
            balls, strikes = count.get('balls'), count.get('strikes')
            strikeout = bool(re.search(r'\bstruck out\b|\bstrikeout\b', description))
            hits = kind in ('single', 'double', 'triple', 'home-run')
            defensive = any(word in description for word in ('double play', 'triple play', 'caught stealing', 'picked off'))
            scoring = bool(item.get('scoringPlay'))
            home, away = item.get('homeScore'), item.get('awayScore')
            close = home is not None and away is not None and abs(int(home) - int(away)) <= 3
            clutch = inning >= 7 and close and (hits or scoring or strikeout or defensive)
            tags = ([kind.replace('-', ' ').title()] if hits else [])
            tags += (['Strikeout'] if strikeout else []) + (['Defensive play'] if defensive else [])
            tags += (['Scoring play'] if scoring else []) + (['Clutch'] if clutch else [])
            plays[play_id] = {
                'play_id': play_id, 'occurred': occurred, 'period': inning,
                'inning': inning, 'inning_half': half, 'period_label': f'{half} {inning}',
                'clock': '', 'clock_seconds': None, 'balls': balls, 'strikes': strikes,
                'count': f'{balls}-{strikes}' if balls is not None and strikes is not None else '',
                'result_count': item.get('resultCount'),
                'outs': item.get('outs'), 'text': text,
                'interesting': bool(hits or scoring or strikeout or defensive),
                'clutch': bool(clutch), 'tags': tags,
                'event_type': 'strikeout' if strikeout else kind,
                'at_bat_id': item.get('atBatId'), 'scoring_play': scoring,
                'home_score': home, 'away_score': away,
            } | sign(pitch_id, item)
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(plays.values(), key=lambda p: (p['occurred'], p['play_id']))


class ScorebugAliases(list):
    """Team alias lists (football/basketball bugs) that also carry the game's players.

    `players` is mlb_roster(payload): baseball bugs name the batter and pitcher, so
    read_scorebug(rows, team_aliases(payload), league='mlb') works unchanged.
    """
    players: list[dict]


def team_aliases(payload: dict) -> ScorebugAliases:
    competitors = payload.get('header', {}).get('competitions', [{}])[0].get('competitors', [])
    aliases = ScorebugAliases([[str(c.get('team', {}).get(key, '')).upper()
                                for key in ('abbreviation', 'location', 'shortDisplayName', 'nickname')
                                if len(str(c.get('team', {}).get(key, ''))) >= 3] for c in competitors])
    aliases.players = mlb_roster(payload)
    return aliases


def read_scorebug(rows: list[dict], aliases: list, league: str | None = None) -> dict | None:
    if league == 'mlb':
        # Baseball has no game countdown (never mistake the pitch timer for one); it is
        # read as batter/pitcher/pitch count/ball-strike count. aliases = mlb_roster().
        players = getattr(aliases, 'players', None)
        if players is None:
            players = [a for a in aliases if isinstance(a, dict)]
        return read_mlb_scorebug(rows, players) if players else None
    # Restrict to one spatially coherent score bug, not ticker scores or ad text.
    for row in rows:
        found = re.search(r'\b(\d{1,2}:[0-5]\d)\b', row['text'])
        if not found or row.get('confidence', 0) < .7:
            continue
        nearby = [r for r in rows if abs(r['y'] - row['y']) < .10]
        text = ' '.join(r['text'].upper() for r in nearby)
        if len(aliases) != 2 or not all(any(re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)', text)
                                          for a in team) for team in aliases):
            continue
        periods = set()
        for candidate in nearby:
            if abs(candidate['x'] - row['x']) > .20:
                continue
            value = candidate['text'].upper()
            # A down marker such as "2ND & 5" is not the quarter.
            if '&' in value or 'DOWN' in value:
                continue
            for match in re.finditer(r'\b(?:([1-4])(?:ST|ND|RD|TH)|Q([1-4]))\b', value):
                periods.add(int(match[1] or match[2]))
        if len(periods) == 1:
            return {'period': periods.pop(), 'clock_seconds': clock_seconds(found[1]), 'clock': found[1]}
    return None


def locate_play(play: dict, observations: list[dict], league: str | None = None) -> dict | None:
    """Find first on-screen appearance of the play's quarter/clock in timestamped video.

    For league='mlb' returns the pitch-aligned clip window from align_mlb_play (with
    'start'/'end' to cut and 'time' = first post-pitch frame) or None; use
    explain_mlb_alignment() for the reason a play is not aligned.
    """
    if league == 'mlb':
        return locate_mlb_play(play, observations)
    if play.get('clock_seconds') is None:
        return None  # Clockless (baseball) plays need league='mlb'.
    matches = [o for o in observations if o['period'] == play['period']
               and o.get('clock_seconds') is not None
               and abs(o['clock_seconds'] - play['clock_seconds']) <= 1
               and -120 <= o['time'] - play['occurred'] <= 600]
    # Never guess a stream delay from when a request happened. A missing match waits.
    return min(matches, key=lambda o: o['time']) if matches else None


def locate_mlb_play(play: dict, observations: list[dict]) -> dict | None:
    """MLB clip window {'start', 'end', 'time', ...} (UTC seconds) or None. See align_mlb_play."""
    return align_mlb_play(play, observations)['window']


def explain_mlb_alignment(play: dict, observations: list[dict]) -> dict:
    """{'window': dict | None, 'reason': str} for status displays and logs."""
    return align_mlb_play(play, observations)
