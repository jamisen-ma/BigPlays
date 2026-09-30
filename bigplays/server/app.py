from __future__ import annotations

"""BigPlays API + live dashboard server.

Endpoints
- GET  /api/status            server mode, counts
- GET  /api/highlights        all highlight records (newest first)
- GET  /api/games             live game states being monitored
- GET  /api/stream            Server-Sent Events: hello, game_tick, pipeline, highlight, log
- GET  /clips/<file>          static mp4 / jpg / json
- GET  /                      React dashboard (frontend/dist) when built
"""

import asyncio
import json
import time
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, List, Optional
from zoneinfo import ZoneInfo

import orjson
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from bigplays.config import settings
from bigplays.server.events import bus
from bigplays.server.streams import router as streams_router, stop_recording
from bigplays.server.agent import router as agent_router
from bigplays.server.social import router as social_router
from bigplays.server.games import router as games_router
from bigplays.storage.catalog import catalog_for

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

_seen_sidecars: dict[str, int] = {}
_mlb_error: str | None = None


def _json_response(data):
    return Response(content=orjson.dumps(data), media_type="application/json")


def load_highlights() -> List[dict]:
    clips_dir = settings.clips_dir
    catalog = catalog_for(clips_dir, settings.database_path)
    catalog.migrate_sidecars(clips_dir)
    items: List[dict] = []
    for data in catalog.all():
        if not data.get("file") or not (clips_dir / data["file"]).exists():
            data["file"] = None
        items.append(data)
    return items


def mlb_status() -> dict:
    date = datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()
    reports = catalog_for(settings.clips_dir, settings.database_path).imports()
    report = reports.get(f'mlb-{date}', {})
    return {'date': date, 'games': report.get('games', []),
            'enabled': settings.mlb_highlights_enabled, 'checked_at': report.get('checked_at'),
            'error': _mlb_error, 'poll_seconds': settings.mlb_highlights_poll_seconds}


def saved_mlb_games() -> list[dict]:
    return mlb_status()['games']


def game_list() -> list[dict]:
    return saved_mlb_games()


async def _watch_mlb() -> None:
    """Collect newly published official clips without depending on a live TV stream."""
    global _mlb_error
    from bigplays.ingest.mlb_archive import import_date
    while True:
        try:
            date = datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()
            await asyncio.to_thread(import_date, date)
            _mlb_error = None
            bus.publish('game_tick', {'games': game_list()})
        except Exception as error:
            _mlb_error = f'MLB highlights check failed: {type(error).__name__}'
            bus.publish('log', {'level': 'error', 'msg': _mlb_error})
        await asyncio.sleep(settings.mlb_highlights_poll_seconds)


def changed_highlights(seen):
    changes = []
    for meta in settings.clips_dir.glob('*.json'):
        stamp = meta.stat().st_mtime_ns
        if seen.get(meta.name) == stamp:
            continue
        try:
            data = orjson.loads(meta.read_bytes())
        except Exception:
            continue
        event = 'highlight_update' if meta.name in seen else 'highlight'
        seen[meta.name] = stamp
        data.setdefault('file', meta.with_suffix('.mp4').name)
        if data.get('event_id'):
            catalog_for(settings.clips_dir, settings.database_path).upsert(data)
        changes.append((event, data))
    return changes


async def _watch_clips_dir() -> None:
    """Publish highlights written by an external agent process (real pipeline)."""
    global _seen_sidecars
    _seen_sidecars = {p.name: p.stat().st_mtime_ns for p in settings.clips_dir.glob("*.json")}
    while True:
        await asyncio.sleep(2.0)
        try:
            for event, data in changed_highlights(_seen_sidecars):
                bus.publish(event, data)
        except Exception as ex:
            bus.publish("log", {"level": "error", "msg": f"watcher: {ex}"})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.clips_dir.mkdir(parents=True, exist_ok=True)
    catalog_for(settings.clips_dir, settings.database_path).migrate_sidecars(settings.clips_dir)
    bus.bind_loop(asyncio.get_running_loop())
    watcher = asyncio.create_task(_watch_clips_dir())
    mlb_watcher = asyncio.create_task(_watch_mlb()) if settings.mlb_highlights_enabled else None
    try:
        yield
    finally:
        watcher.cancel()
        if mlb_watcher:
            mlb_watcher.cancel()
        await stop_recording()


app = FastAPI(title="BigPlays", lifespan=lifespan)
app.include_router(games_router)  # before the legacy /api/games route so the contract endpoint wins
app.include_router(streams_router)
app.include_router(agent_router)
app.include_router(social_router)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

settings.clips_dir.mkdir(parents=True, exist_ok=True)
app.mount("/clips", StaticFiles(directory=str(settings.clips_dir)), name="clips")


@app.get("/api/status")
def api_status():
    return _json_response({
        "mode": "live",
        "leagues": settings.leagues,
        "use_llm": settings.use_llm,
        "llm_model": settings.llm_model,
        "s3": {"enabled": settings.enable_s3, "bucket": settings.s3_bucket, "prefix": settings.s3_prefix},
        "highlights": len(load_highlights()),
        "catalog": {"storage": "sqlite", "persistent": True,
                    "imports": catalog_for(settings.clips_dir, settings.database_path).imports()},
        "mlb": mlb_status(),
        "server_time": time.time(),
    })


@app.get("/api/highlights")
def api_highlights():
    return _json_response(load_highlights())


@app.get("/api/games")
def api_games():
    return _json_response(game_list())


@app.get('/api/mlb/games')
def api_mlb_games():
    return _json_response(mlb_status())


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {orjson.dumps(data).decode()}\n\n".encode()


@app.get("/api/stream")
async def api_stream(request: Request):
    q = bus.subscribe()

    async def gen() -> AsyncIterator[bytes]:
        try:
            yield _sse("hello", {
                "mode": "live",
                "games": game_list(),
                "recent": load_highlights(),
                "pipeline": [e.data | {"ts": e.ts} for e in bus.recent(["pipeline"], 40)],
            })
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                yield _sse(ev.type, ev.data | {"ts": ev.ts})
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- frontend
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path == 'api' or full_path.startswith('api/'):
        return JSONResponse({'ok': False, 'error': 'Not found'}, status_code=404)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
    return HTMLResponse(
        "<h1>BigPlays API</h1><p>Frontend not built. Run <code>cd frontend && npm run dev</code> "
        "for the live dashboard, or <code>npm run build</code> to serve it from here.</p>"
        "<p><a href='/api/highlights'>/api/highlights</a> · <a href='/api/stream'>/api/stream</a></p>"
    )
