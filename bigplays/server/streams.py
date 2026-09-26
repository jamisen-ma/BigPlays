"""Same-origin gateway to the private Node resolver and the existing clip buffer."""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from bigplays.config import settings
from bigplays.media.stream_buffer import StreamBuffer, StreamBufferConfig
from bigplays.media.hls_clipper import clip_window

router = APIRouter()
recording: StreamBuffer | None = None
recording_started: datetime | None = None
lock = asyncio.Lock()


def failure(stage, error, status=400):
    return JSONResponse({'ok': False, 'stage': stage, 'error': error}, status_code=status)


def relay_path(raw):
    u = urlsplit(raw)
    base = urlsplit(settings.resolver_base_url)
    if (u.netloc and (u.scheme, u.netloc) != (base.scheme, base.netloc)) or u.path != '/api/hls' or not u.query or u.fragment:
        raise ValueError('Invalid resolver relay URL')
    return u.path + '?' + u.query


class ResolveInput(BaseModel):
    url: str = Field(max_length=512)
    record: bool = False


@router.post('/api/stream')
async def resolve_stream(body: ResolveInput):
    global recording, recording_started
    if not re.fullmatch(r'https://ppv\.to/live/(?:24/)?[A-Za-z0-9_-]+/?', body.url):
        return failure('input', 'Expected https://ppv.to/live/event-slug')
    if not settings.resolver_api_key:
        return failure('config', 'Set RESOLVER_API_KEY on the backend and resolver', 503)
    if body.record and settings.demo_mode:
        return failure('record', 'Turn off DEMO_MODE before recording a live event')
    async with lock:
        if body.record and recording and recording.proc and recording.proc.poll() is None:
            return failure('record', 'Stop the current recording before switching events', 409)
        try:
            async with httpx.AsyncClient(timeout=50, follow_redirects=False, trust_env=False) as client:
                response = await client.post(settings.resolver_base_url.rstrip('/') + '/api/stream',
                    json={'url': body.url}, headers={'Authorization': f'Bearer {settings.resolver_api_key}'})
                data = response.json()
                if data.get('ok') is False:
                    return JSONResponse({'ok': False, 'stage': str(data.get('stage', 'resolver')), 'error': str(data.get('error', 'Resolution failed'))})
                response.raise_for_status()
                if data.get('ok') is not True:
                    raise ValueError('Invalid resolver response')
                path = relay_path(data['proxiedUrl'])
                if body.record:
                    recording = StreamBuffer(StreamBufferConfig(
                        stream_url=settings.resolver_base_url.rstrip('/') + path,
                        buffer_dir=settings.buffer_dir))
                    recording_started = datetime.now(timezone.utc)
                    await asyncio.to_thread(recording.start)
                return {'ok': True, 'proxiedUrl': path, 'recording': body.record}
        except httpx.TimeoutException:
            return failure('timeout', 'Resolver request timed out', 504)
        except (httpx.HTTPError, ValueError, KeyError):
            return failure('resolver', 'Resolver unavailable or returned an invalid response', 502)
        except Exception:
            return failure('record', 'Unable to start FFmpeg recording', 500)


@router.get('/api/hls')
async def relay(request: Request):
    # Fixed destination only. Node validates the signature and every upstream host.
    if len(request.url.query) > 8192:
        return failure('relay', 'Query too large')
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = await client.get(settings.resolver_base_url.rstrip('/') + '/api/hls', params=request.query_params)
        if response.status_code != 200:
            return failure('relay', 'Relay rejected the request or upstream failed', response.status_code if response.status_code in (403, 404) else 502)
        return Response(response.content, media_type=response.headers.get('content-type', 'application/octet-stream'),
                        headers={'Cache-Control': 'no-store'})
    except httpx.TimeoutException:
        return failure('timeout', 'HLS request timed out', 504)
    except httpx.HTTPError:
        return failure('relay', 'Resolver unavailable', 502)


@router.get('/api/recording')
async def recording_status():
    if recording:
        await asyncio.to_thread(recording.prune_old)
    running = bool(recording and recording.proc and recording.proc.poll() is None)
    return {'ok': True, 'recording': running,
            'error': 'FFmpeg exited; resolve the event again' if recording and not running else None}


@router.post('/api/recording/stop')
async def stop_recording():
    global recording
    async with lock:
        if recording:
            await asyncio.to_thread(recording.stop)
            recording = None
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
        end = datetime.strptime(segments[-1].stem, '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc)
        end -= timedelta(microseconds=1)
        start = end - timedelta(seconds=body.seconds)
        if recording_started and start < recording_started:
            return failure('clip', 'Buffer is still filling; retry shortly')
        event_id = uuid.uuid4().hex
        out = settings.clips_dir / f'{event_id}.mp4'
        metadata = {'event_id': event_id, 'game_id': 'live', 'league': 'nba',
                    'occurred_utc': end.isoformat(), 'clip_start_utc': start.isoformat(),
                    'clip_end_utc': end.isoformat(), 'title': body.title, 'tags': ['manual'],
                    'reasons': ['manual'], 'base_score': 1.0, 'combined_score': 1.0}
        try:
            await asyncio.to_thread(clip_window, recording.segments_dir, start, end, out, metadata)
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
            _discovery_cache = {'ok': True, 'games': await discover()}
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            _discovery_cache = {'ok': False, 'stage': 'discovery', 'error': 'Live-game catalog unavailable. ESPN or PPV could not be reached or returned an unsupported response.'}
        _discovery_expires = time.monotonic() + 60
        return _discovery_cache
