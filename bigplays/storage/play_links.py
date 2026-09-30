"""Link saved clips (SQLite highlight catalog) to ESPN play-by-play plays.

Read-only against the catalog: a cheap `SELECT count(*), max(updated_utc)` decides whether the
in-memory index must be rebuilt, so new live clips show up on the next request (or the next
watcher tick) without re-parsing every payload on every call.

Matching (no time-based fuzzy matching, ever):
  (a) exact: the clip's `source_play_id` / `play_id` equals `play.play_id` or is in
      `play.related_play_ids`;
  (b) candidates: any entry of the clip's `play_candidates` matches the same way;
  (c) MLB only, for official MLB uploads keyed by MLB GUIDs (ESPN has no such ids): an exact
      game-state match (inning, half, score after the play, pre-pitch count, outs) that must be
      unique within the game (ties broken only by the batter's surname in the clip text); otherwise
  (d) the clip goes to `clips_unmatched`.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from bigplays.config import settings

SOURCE_KINDS = ('live_capture', 'replay', 'official_upload')
KIND_RANK = {'live_capture': 2, 'official_upload': 1, 'replay': 0}

# ESPN abbreviation -> MLB Stats API abbreviation, where they differ.
MLB_ABBR = {'CHW': 'CWS', 'ARI': 'AZ', 'OAK': 'ATH', 'WSN': 'WSH', 'KCR': 'KC', 'SDP': 'SD',
            'SFG': 'SF', 'TBR': 'TB'}


def database_path() -> Path:
    return Path(settings.database_path or settings.clips_dir.with_suffix('.sqlite3'))


def source_kind_of(record: dict) -> str:
    kind = record.get('source_kind')
    if kind in SOURCE_KINDS:
        return kind
    if record.get('demo'):
        return 'replay'
    if record.get('imported'):
        return 'official_upload'
    return 'live_capture'


def _num(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _duration(record: dict):
    for key in ('duration_seconds', 'clip_duration'):
        if _num(record.get(key)) is not None:
            return _num(record[key])
    start, end = _num(record.get('video_start')), _num(record.get('video_end'))
    if start is not None and end is not None and end > start:
        return round(end - start, 3)
    try:
        a = datetime.fromisoformat(str(record['clip_start_utc']).replace('Z', '+00:00'))
        b = datetime.fromisoformat(str(record['clip_end_utc']).replace('Z', '+00:00'))
        return round((b - a).total_seconds(), 3)
    except (KeyError, ValueError, TypeError):
        return None


def _social_score(record: dict):
    score = record.get('social_score')
    if score is None and isinstance(record.get('social'), dict):
        score = record['social'].get('score')
    return _num(score)


def public_fan_hype(record: dict) -> dict | None:
    """v1.2: the hype gate's evidence for a clip, trimmed for clients (comment text only, no usernames)."""
    hype = record.get('fan_hype')
    if not isinstance(hype, dict):
        return None
    llm = hype.get('llm') if isinstance(hype.get('llm'), dict) else {}
    out = {'status': hype.get('status'), 'decision': hype.get('decision'), 'score': _num(hype.get('score')),
           'pre_score': _num(hype.get('pre_score')), 'cant_miss': hype.get('cant_miss'),
           'llm_viral': llm.get('viral'), 'llm_reason': llm.get('reason'),
           'quotes': [q for q in hype.get('quotes') or [] if isinstance(q, str)][:3]}
    return {k: v for k, v in out.items() if v not in (None, [])}


def clip_ref(record: dict, clips_dir: Path | None = None) -> dict:
    """ClipRef (contract) + link keys. URLs follow /api/highlights + frontend util.ts/Player.tsx:
    video `/clips/<file>` (only when the file exists), poster = YouTube thumb or `/clips/<poster>`."""
    clips_dir = Path(clips_dir or settings.clips_dir)
    file = record.get('file')
    if file and not (clips_dir / file).exists():
        file = None
    youtube_id = record.get('youtube_id')
    poster = record.get('poster')
    if youtube_id:
        poster_url = f'https://i.ytimg.com/vi/{youtube_id}/mqdefault.jpg'
    else:
        poster_url = f'/clips/{poster}' if poster else None
    candidates = record.get('play_candidates') or []
    return {
        'event_id': record.get('event_id'),
        'title': record.get('title'),
        'video_url': f'/clips/{file}' if file else None,
        'poster_url': poster_url,
        'duration_seconds': _duration(record),
        'source_kind': source_kind_of(record),
        'social_score': _social_score(record),
        'occurred_utc': record.get('occurred_utc'),
        'published_utc': record.get('published_utc') or record.get('received_utc'),
        'youtube_id': youtube_id,
        'fan_hype': public_fan_hype(record),
        # link keys (not part of the public ClipRef, stripped by public_clip())
        '_league': (record.get('league') or '').lower(),
        '_game_id': str(record.get('game_id') or ''),
        '_play_ids': [str(x) for x in (record.get('source_play_id'), record.get('play_id')) if x],
        '_candidates': [str(x) for x in candidates if x],
        '_date': record.get('date'),
        '_away': record.get('away'), '_home': record.get('home'),
        '_mlb_state': {k: record.get(k) for k in ('inning', 'inning_half', 'away_score', 'home_score',
                                                   'balls', 'strikes', 'outs')},
        '_text': ' '.join(str(record.get(k) or '') for k in ('title', 'description', 'player')).lower(),
        '_sort_time': record.get('received_utc') or record.get('published_utc') or record.get('occurred_utc') or '',
    }


def public_clip(clip: dict) -> dict:
    return {k: v for k, v in clip.items() if not k.startswith('_')}


class LinkIndex:
    def __init__(self, path: Path | None = None, clips_dir: Path | None = None):
        self._path = Path(path) if path else None
        self._clips_dir = Path(clips_dir) if clips_dir else None
        self._lock = threading.Lock()
        self._key = None
        self._by_game: dict[tuple[str, str], list[dict]] = {}
        self._by_mlb_matchup: dict[tuple[str, str, str], list[dict]] = {}
        self._event_games: dict[str, tuple[str, str]] = {}

    @property
    def path(self) -> Path:
        return self._path or database_path()

    @property
    def clips_dir(self) -> Path:
        return self._clips_dir or Path(settings.clips_dir)

    def _connect(self):
        return sqlite3.connect(f'file:{self.path}?mode=ro', uri=True, timeout=2)

    def refresh(self) -> list[tuple[str, str]]:
        """Rebuild when the catalog changed. Returns (league, game_id) pairs that gained clips."""
        path = self.path
        if not path.exists():
            with self._lock:
                self._key, self._by_game, self._by_mlb_matchup = None, {}, {}
            return []
        db = self._connect()
        try:
            key = (str(path),) + tuple(db.execute('SELECT count(*), max(updated_utc) FROM highlights').fetchone())
            if key == self._key:
                return []
            rows = db.execute('SELECT payload FROM highlights').fetchall()
        except sqlite3.OperationalError:
            return []  # missing table / locked: keep serving the previous index
        finally:
            db.close()
        by_game: dict = {}
        by_matchup: dict = {}
        event_games: dict = {}
        for (payload,) in rows:
            try:
                record = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict) or not record.get('event_id') or not record.get('game_id'):
                continue
            clip = clip_ref(record, self.clips_dir)
            game = (clip['_league'], clip['_game_id'])
            by_game.setdefault(game, []).append(clip)
            event_games[clip['event_id']] = game
            if clip['_league'] == 'mlb' and clip['_date'] and clip['_away'] and clip['_home']:
                by_matchup.setdefault((clip['_date'], clip['_away'], clip['_home']), []).append(clip)
        with self._lock:
            previous = self._event_games
            changed = [] if self._key is None else sorted(
                {g for e, g in event_games.items() if e not in previous})
            self._key, self._by_game, self._by_mlb_matchup, self._event_games = key, by_game, by_matchup, event_games
        return changed

    def clips_for_game(self, league: str, game_id: str, game: dict | None = None) -> list[dict]:
        """Clips stored under this game id. For MLB, `game` (an ESPN Game dict) also pulls in
        official MLB imports stored under the MLB gamePk, matched by date + away/home teams."""
        self.refresh()
        league = league.lower()
        with self._lock:
            clips = list(self._by_game.get((league, str(game_id)), []))
            if league == 'mlb' and game:
                seen = {c['event_id'] for c in clips}
                for key in mlb_matchup_keys(game):
                    for clip in self._by_mlb_matchup.get(key, []):
                        if clip['event_id'] not in seen:
                            clips.append(clip)
                            seen.add(clip['event_id'])
        return clips


def _abbr(team) -> str | None:
    if isinstance(team, dict):
        team = team.get('abbr') or team.get('abbreviation')
    return str(team).upper() if team else None


def mlb_matchup_keys(game: dict) -> list[tuple[str, str, str]]:
    away, home = _abbr(game.get('away')), _abbr(game.get('home'))
    start = game.get('start_utc')
    if not (away and home and start):
        return []
    try:
        when = datetime.fromisoformat(str(start).replace('Z', '+00:00'))
    except ValueError:
        return []
    dates = {when.astimezone(ZoneInfo(tz)).date().isoformat() for tz in ('America/New_York', 'America/Los_Angeles')}
    return [(d, MLB_ABBR.get(away, away), MLB_ABBR.get(home, home)) for d in sorted(dates)]


def _half(label) -> str | None:
    label = str(label or '').strip().lower()
    if label.startswith('top'):
        return 'top'
    if label.startswith(('bot', 'bottom')):
        return 'bottom'
    return None


def _mlb_state_matches(clip: dict, play: dict) -> bool:
    state = clip['_mlb_state']
    mlb = play.get('mlb') or {}
    half = _half(state.get('inning_half'))
    if None in (state.get('inning'), half, state.get('away_score'), state.get('home_score')):
        return False
    if play.get('period') != state['inning'] or _half(play.get('period_label')) != half:
        return False
    if play.get('away_score') != state['away_score'] or play.get('home_score') != state['home_score']:
        return False
    for key in ('balls', 'strikes'):
        if state.get(key) is None or mlb.get(key) is None or mlb[key] != state[key]:
            return False
    if state.get('outs') is not None and mlb.get('outs') is not None and mlb['outs'] not in (state['outs'], state['outs'] + 1):
        return False  # ESPN may report outs after the play; MLB clips report outs before the pitch
    return True


def _batter_named(clip: dict, play: dict) -> bool:
    batter = ((play.get('mlb') or {}).get('batter') or '').strip().lower()
    surname = batter.split()[-1] if batter else ''
    return bool(surname) and surname in (clip.get('_text') or '')


def _rank(clip: dict):
    score = clip.get('social_score')
    return (KIND_RANK.get(clip.get('source_kind'), 0), score if score is not None else -1.0, clip.get('_sort_time') or '')


def viral_reason(clip: dict) -> str:
    reason = {'live_capture': 'Live capture', 'official_upload': 'Official highlight',
              'replay': 'Replay clip'}.get(clip.get('source_kind'), 'Clip')
    if clip.get('social_score') is not None:
        reason += f" · fan buzz {clip['social_score']:.2f}"
    return reason


def attach(plays: list[dict], clips: Iterable[dict], league: str | None = None) -> tuple[list[dict], list[dict]]:
    """Return (plays with clip/viral fields filled, unmatched public ClipRefs). Inputs are not mutated."""
    plays = [dict(p) for p in plays]
    primary: dict[str, int] = {}
    related: dict[str, int] = {}
    for index, play in enumerate(plays):
        if play.get('play_id') is not None:
            primary.setdefault(str(play['play_id']), index)
        for rid in play.get('related_play_ids') or []:
            related.setdefault(str(rid), index)

    def lookup(ids):
        for pid in ids:
            if pid in primary:
                return primary[pid]
        for pid in ids:
            if pid in related:
                return related[pid]
        return None

    assigned: dict[int, list[dict]] = {}
    unmatched: list[dict] = []
    for clip in clips:
        index = lookup(clip.get('_play_ids') or [])
        method = 'exact'
        if index is None:
            index, method = lookup(clip.get('_candidates') or []), 'candidate'
        if index is None and (league or clip.get('_league')) == 'mlb' and clip.get('_mlb_state'):
            hits = [i for i, p in enumerate(plays) if _mlb_state_matches(clip, p)]
            if len(hits) > 1:  # same state twice (e.g. two 2-2 strikeouts): the named batter decides
                hits = [i for i in hits if _batter_named(clip, plays[i])]
            if len(hits) == 1:
                index, method = hits[0], 'mlb_game_state'
        if index is None:
            unmatched.append(public_clip(clip))
        else:
            assigned.setdefault(index, []).append(dict(clip, _match=method))

    for play in plays:
        play.setdefault('clip', None)
        play.setdefault('viral', False)
        play.setdefault('viral_reason', None)
    for index, group in assigned.items():
        group.sort(key=_rank, reverse=True)
        best = group[0]
        play = plays[index]
        play['clip'] = public_clip(best) | {'match_method': best['_match']}
        play['alternate_clips'] = [public_clip(c) | {'match_method': c['_match']} for c in group[1:]]
        play['viral'] = True
        play['viral_reason'] = viral_reason(best)
    unmatched.sort(key=lambda c: c.get('occurred_utc') or '')
    return plays, unmatched


def viral_play_count(clips: Iterable[dict]) -> int:
    """Distinct plays with clips, without fetching play-by-play (for scoreboard counts)."""
    keys = set()
    for clip in clips:
        ids = clip.get('_play_ids') or clip.get('_candidates') or [clip.get('event_id')]
        keys.add(ids[0])
    return len(keys)


index = LinkIndex()


def clips_for_game(league: str, game_id: str, game: dict | None = None) -> list[dict]:
    return index.clips_for_game(league, game_id, game)
