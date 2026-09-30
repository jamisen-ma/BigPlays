"""Same-origin gateway to the private Node resolver and the existing clip buffer."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from bigplays.config import settings
from bigplays.media.stream_buffer import StreamBuffer, StreamBufferConfig
from bigplays.media.hls_clipper import clip_recent
from bigplays.ingest.ppv_provider import valid_event_url

router = APIRouter()
recording: StreamBuffer | None = None
recording_started: datetime | None = None
recording_game: dict | None = None
lock = asyncio.Lock()


def failure(stage, error, status=400):
    return JSONResponse({'ok': False, 'stage': stage, 'error': error}, status_code=status)


def relay_path(raw):
    if not isinstance(raw, str):
        raise ValueError('Invalid resolver relay URL')
    u = urlsplit(raw)
    base = urlsplit(settings.resolver_base_url)
    if (u.netloc and (u.scheme, u.netloc) != (base.scheme, base.netloc)) or u.path != '/api/hls' or not u.query or u.fragment:
        raise ValueError('Invalid resolver relay URL')
    return u.path + '?' + u.query


class GameContext(BaseModel):
    game_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    league: Literal['nba', 'nfl', 'ncaaf', 'mlb']
    name: str = Field(default='', max_length=200)


class ResolveInput(BaseModel):
    url: str = Field(max_length=512)
    record: bool = False
    game: GameContext | None = None


@router.post('/api/stream')
async def resolve_stream(body: ResolveInput):
    global recording, recording_started, recording_game
    if not valid_event_url(body.url):
        return failure('input', 'Expected an HTTPS ppv.st/live/... or ppv.to/live/... event URL')
    if not settings.resolver_api_key:
        return failure('config', 'Set RESOLVER_API_KEY on the backend and resolver', 503)
    async with lock:
        if body.record and recording and recording.proc and recording.proc.poll() is None:
            return failure('record', 'Stop the current recording before switching events', 409)
        try:
            async with httpx.AsyncClient(timeout=50, follow_redirects=False, trust_env=False) as client:
                response = await client.post(settings.resolver_base_url.rstrip('/') + '/api/stream',
                    json={'url': body.url}, headers={'Authorization': f'Bearer {settings.resolver_api_key}'})
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError('Invalid resolver response')
                if data.get('ok') is False:
                    return JSONResponse({'ok': False, 'stage': str(data.get('stage', 'resolver')), 'error': str(data.get('error', 'Resolution failed'))})
                response.raise_for_status()
                if data.get('ok') is not True:
                    raise ValueError('Invalid resolver response')
                path = relay_path(data['proxiedUrl'])
                if body.record:
                    candidate = StreamBuffer(StreamBufferConfig(
                        stream_url=settings.resolver_base_url.rstrip('/') + path,
                        # HLS downloads arrive in bursts. Pace ingestion so the
                        # wall-clock segment filenames cannot overwrite one another.
                        buffer_dir=settings.buffer_dir, realtime=True))
                    try:
                        await asyncio.to_thread(candidate.start)
                    except Exception:
                        await asyncio.to_thread(candidate.stop)
                        raise
                    recording = candidate
                    recording_started = datetime.now(timezone.utc)
                    recording_game = body.game.model_dump() if body.game else {
                        'game_id': 'manual-' + uuid.uuid4().hex, 'league': 'unknown', 'name': ''}
                return {'ok': True, 'proxiedUrl': path,
                        'recording': bool(recording and recording.proc and recording.proc.poll() is None)}
        except httpx.TimeoutException:
            return failure('timeout', 'Resolver request timed out', 504)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return failure('resolver', 'Resolver unavailable or returned an invalid response', 502)
        except Exception:
            return failure('record', 'Unable to start FFmpeg recording', 500)


@router.get('/api/hls')
async def relay(request: Request):
    # Fixed destination only. Node validates the signature and every upstream host.
    if len(request.url.query) > 8192:
        return failure('relay', 'Query too large')
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False) as client:
            response = await client.get(settings.resolver_base_url.rstrip('/') + '/api/hls', params=request.query_params,
                                        headers={'Range': request.headers['range']} if 'range' in request.headers else {})
        if response.status_code not in (200, 206):
            return failure('relay', 'Relay rejected the request or upstream failed', response.status_code if response.status_code in (403, 404, 416, 503) else 502)
        return Response(response.content, status_code=response.status_code,
                        media_type=response.headers.get('content-type', 'application/octet-stream'),
                        headers={'Cache-Control': 'no-store', **{key: response.headers[key]
                            for key in ('content-range', 'accept-ranges') if key in response.headers}})
    except httpx.TimeoutException:
        return failure('timeout', 'HLS request timed out', 504)
    except httpx.HTTPError:
        return failure('relay', 'Resolver unavailable', 502)


@router.get('/api/recording')
async def recording_status():
    async with lock:
        if recording:
            await asyncio.to_thread(recording.prune_old)
        running = bool(recording and recording.proc and recording.proc.poll() is None)
        return {'ok': True, 'recording': running, 'game': recording_game,
                'error': 'FFmpeg exited; resolve the event again' if recording and not running else None}


@router.post('/api/recording/stop')
async def stop_recording():
    global recording, recording_started, recording_game
    async with lock:
        if recording:
            await asyncio.to_thread(recording.stop)
            recording = None
        recording_started = None
        recording_game = None
    return {'ok': True, 'recording': False}


class ClipInput(BaseModel):
    seconds: int = Field(default=14, ge=3, le=120)
    title: str = Field(default='Live highlight', min_length=1, max_length=200)


@router.post('/api/recording/clip')
async def cut_clip(body: ClipInput):
    async with lock:
        if not recording or not recording.proc or recording.proc.poll() is not None:
            return failure('clip', 'Start recording and allow the buffer to fill first')
        # Exclude the segment currently being written by FFmpeg.
        segments = recording.list_segments()
        if len(segments) < 2:
            return failure('clip', 'Buffer is still filling; retry shortly')
        completed = [segment for segment in segments[:-1] if recording_started and
                     datetime.strptime(segment.stem, '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc)
                     >= recording_started.replace(microsecond=0)]
        end = datetime.strptime(segments[-1].stem, '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc)
        end -= timedelta(microseconds=1)
        start = end - timedelta(seconds=body.seconds)
        if recording_started and start < recording_started:
            return failure('clip', 'Buffer is still filling; retry shortly')
        event_id = uuid.uuid4().hex
        out = settings.clips_dir / f'{event_id}.mp4'
        metadata = {'event_id': event_id, **(recording_game or {}),
                    'occurred_utc': end.isoformat(), 'clip_start_utc': start.isoformat(),
                    'clip_end_utc': end.isoformat(), 'title': body.title, 'tags': ['manual'],
                    'reasons': ['manual'], 'base_score': 1.0, 'combined_score': 1.0}
        try:
            await asyncio.to_thread(clip_recent, completed, body.seconds, out, metadata)
            return {'ok': True, 'file': '/clips/' + out.name}
        except Exception:
            return failure('clip', 'Could not cut the buffer; allow more recording time and retry', 422)


_discovery_cache = None
_discovery_expires = 0.0
_discovery_lock = asyncio.Lock()


@router.get('/api/live-streams')
async def live_streams():
    import time
    from bigplays.ingest.live_streams import discover
    global _discovery_cache, _discovery_expires
    async with _discovery_lock:
        if _discovery_cache is not None and time.monotonic() < _discovery_expires:
            return _discovery_cache
        try:
            _discovery_cache = await discover()
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            _discovery_cache = {'ok': False, 'stage': 'discovery', 'error': 'Live-game catalog unavailable. ESPN or PPV could not be reached or returned an unsupported response.'}
        _discovery_expires = time.monotonic() + 60
        return _discovery_cache
