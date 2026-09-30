"""Scoreboard + play-by-play API (docs/games-api-contract.md) with saved clips linked to plays.

- GET /api/games?league=nfl|mlb&date=YYYYMMDD[&week=N&season=YYYY]
- GET /api/games/{league}/{game_id}

ESPN data comes from bigplays.ingest.gamecast (async, cached there). Catalog reads run in a
worker thread against a read-only SQLite connection, so nothing here blocks the clipping pipeline.
When a new clip lands for a game, a `game_update {league, game_id}` SSE event is published.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib
import inspect
import logging
from typing import Any

import orjson
from fastapi import APIRouter
from fastapi.responses import Response

from bigplays.server.events import bus
from bigplays.storage import play_links

log = logging.getLogger(__name__)
router = APIRouter()
LEAGUES = ('nfl', 'mlb')
WATCH_SECONDS = 3.0
_watcher: asyncio.Task | None = None


class ApiError(Exception):
    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status, self.error = status, error


def _json(data, status: int = 200) -> Response:
    return Response(content=orjson.dumps(data, default=str), media_type='application/json', status_code=status)


def _error(status: int, error: str) -> Response:
    return _json({'ok': False, 'error': error}, status)


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _gamecast():
    try:
        return importlib.import_module('bigplays.ingest.gamecast')
    except ImportError as error:
        raise ApiError(503, f'gamecast data layer unavailable: {error}') from error


def _as_dict(value: Any) -> Any:
    if hasattr(value, 'model_dump'):
        return value.model_dump()
    if hasattr(value, 'dict') and not isinstance(value, dict):
        return value.dict()
    return value


def _league(league: str) -> str:
    league = (league or '').lower()
    if league not in LEAGUES:
        raise ApiError(400, f"league must be one of {', '.join(LEAGUES)}")
    return league


def _upstream_error(error: Exception) -> ApiError:
    chain, seen = error, set()
    while chain is not None and id(chain) not in seen:  # gamecast wraps httpx errors
        seen.add(id(chain))
        status = getattr(getattr(chain, 'response', None), 'status_code', None)
        if status in (400, 404) or isinstance(chain, LookupError):
            return ApiError(404, 'game not found')
        if isinstance(chain, ValueError):
            return ApiError(400, str(chain))
        chain = chain.__cause__ or chain.__context__
    return ApiError(502, f'upstream data error: {type(error).__name__}')


def _ensure_watcher() -> None:
    """Lazily start a background poll that emits game_update when new clips land."""
    global _watcher
    if _watcher and not _watcher.done():
        return
    try:
        _watcher = asyncio.get_running_loop().create_task(_watch())
    except RuntimeError:
        pass


async def _watch() -> None:
    while True:
        try:
            for league, game_id in await asyncio.to_thread(play_links.index.refresh):
                bus.publish('game_update', {'league': league, 'game_id': game_id})
        except Exception as error:  # never let the watcher die or affect the pipeline
            log.debug('game_update watcher: %s', error)
        await asyncio.sleep(WATCH_SECONDS)


def _game_clips(league: str, game: dict) -> list[dict]:
    return play_links.clips_for_game(league, str(game.get('game_id')), game)


def _with_counts(league: str, games: list[dict]) -> list[dict]:
    out = []
    for game in games:
        clips = _game_clips(league, game)
        out.append(dict(game, clip_count=len(clips), viral_count=play_links.viral_play_count(clips)))
    return out


@router.get('/api/games')
async def api_games(league: str = 'nfl', date: str | None = None, week: int | None = None,
                    season: int | None = None):
    try:
        league = _league(league)
        if date is not None and (len(date) != 8 or not date.isdigit()):
            raise ApiError(400, 'date must be YYYYMMDD')
        if (week is not None or season is not None) and league != 'nfl':
            raise ApiError(400, 'week/season are only supported for nfl')
        gamecast = _gamecast()
        kwargs: dict[str, Any] = {}
        if week is not None or season is not None:
            params = inspect.signature(gamecast.fetch_scoreboard).parameters
            if 'week' not in params and not any(p.kind == p.VAR_KEYWORD for p in params.values()):
                raise ApiError(400, 'week/season lookup is not supported by the data layer yet')
            kwargs = {k: v for k, v in (('week', week), ('season', season)) if v is not None}
        try:
            games = await gamecast.fetch_scoreboard(league, date, **kwargs)
        except ApiError:
            raise
        except Exception as error:
            raise _upstream_error(error) from error
        games = [_as_dict(g) for g in games or []]
        games = await asyncio.to_thread(_with_counts, league, games)
        _ensure_watcher()
        if date is None:
            from zoneinfo import ZoneInfo
            date = datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%Y%m%d')
        return _json({'ok': True, 'league': league, 'date': date, 'week': week, 'season': season,
                      'updated_utc': _now(), 'games': games})
    except ApiError as error:
        return _error(error.status, error.error)


def _detail(league: str, game_id: str, detail: dict) -> dict:
    game = dict(_as_dict(detail.get('game')) or {})
    game.setdefault('game_id', game_id)
    plays = [_as_dict(p) for p in detail.get('plays') or []]
    clips = _game_clips(league, game)
    plays, unmatched = play_links.attach(plays, clips, league)
    game = dict(game, clip_count=len(clips), viral_count=sum(1 for p in plays if p.get('viral')))
    return {'ok': True, 'game': game, 'linescore': _as_dict(detail.get('linescore')),
            'updated_utc': _now(), 'plays': plays, 'clips_unmatched': unmatched}


@router.get('/api/games/{league}/{game_id}')
async def api_game(league: str, game_id: str):
    try:
        league = _league(league)
        if not game_id.isalnum():
            raise ApiError(400, 'invalid game_id')
        gamecast = _gamecast()
        try:
            detail = await gamecast.fetch_game(league, game_id)
        except Exception as error:
            raise _upstream_error(error) from error
        if not detail or not detail.get('game'):
            raise ApiError(404, 'game not found')
        body = await asyncio.to_thread(_detail, league, game_id, _as_dict(detail))
        _ensure_watcher()
        return _json(body)
    except ApiError as error:
        return _error(error.status, error.error)
