from __future__ import annotations

"""BigPlays API + live dashboard server.

Endpoints
- GET  /api/status            server mode, counts
- GET  /api/highlights        all highlight records (newest first)
- GET  /api/games             live game states being monitored
- GET  /api/stream            Server-Sent Events: hello, game_tick, pipeline, highlight, log
- POST /api/demo/next         (demo mode) fire the next play immediately
- GET  /clips/<file>          static mp4 / jpg / json
- GET  /                      React dashboard (frontend/dist) when built
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, List, Optional

import orjson
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from bigplays.config import settings
from bigplays.server.events import bus

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

_sim = None  # DemoSimulator when demo mode is on
_seen_sidecars: set[str] = set()


def _json_response(data):
    return Response(content=orjson.dumps(data), media_type="application/json")


def load_highlights() -> List[dict]:
    clips_dir = settings.clips_dir
    items: List[dict] = []
    for meta in clips_dir.glob("*.json"):
        try:
            data = orjson.loads(meta.read_bytes())
        except Exception:
            continue
        data.setdefault("file", meta.with_suffix(".mp4").name)
        if not (clips_dir / data["file"]).exists():
            data["file"] = None
        poster = meta.with_suffix(".jpg")
        data.setdefault("poster", poster.name if poster.exists() else None)
        items.append(data)
    items.sort(key=lambda d: d.get("occurred_utc", ""), reverse=True)
    return items


async def _watch_clips_dir() -> None:
    """Publish highlights written by an external agent process (real pipeline)."""
    global _seen_sidecars
    _seen_sidecars = {p.name for p in settings.clips_dir.glob("*.json")}
    while True:
        await asyncio.sleep(2.0)
        try:
            for meta in settings.clips_dir.glob("*.json"):
                if meta.name in _seen_sidecars or (_sim and meta.name in _sim.seen_files):
                    continue
                _seen_sidecars.add(meta.name)
                try:
                    data = orjson.loads(meta.read_bytes())
                except Exception:
                    continue
                data.setdefault("file", meta.with_suffix(".mp4").name)
                bus.publish("highlight", data)
        except Exception as ex:
            bus.publish("log", {"level": "error", "msg": f"watcher: {ex}"})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _sim
    settings.clips_dir.mkdir(parents=True, exist_ok=True)
    bus.bind_loop(asyncio.get_running_loop())
    watcher = asyncio.create_task(_watch_clips_dir())
    if settings.demo_mode:
        from bigplays.demo.simulator import DemoSimulator

        _sim = DemoSimulator(
            bus, settings.clips_dir, settings.demo_clips_dir,
            min_interval=settings.demo_min_interval, max_interval=settings.demo_max_interval,
        )
        removed = _sim.purge_previous()
        _sim.start()
        bus.publish("log", {"level": "info", "msg": f"demo simulator started (purged {removed} old demo clips)"})
    try:
        yield
    finally:
        watcher.cancel()
        if _sim:
            _sim.stop()


app = FastAPI(title="BigPlays", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

settings.clips_dir.mkdir(parents=True, exist_ok=True)
app.mount("/clips", StaticFiles(directory=str(settings.clips_dir)), name="clips")


@app.get("/api/status")
def api_status():
    return _json_response({
        "mode": "demo" if settings.demo_mode else "live",
        "leagues": settings.leagues,
        "use_llm": settings.use_llm,
        "llm_model": settings.llm_model,
        "s3": {"enabled": settings.enable_s3, "bucket": settings.s3_bucket, "prefix": settings.s3_prefix},
        "highlights": len(load_highlights()),
        "server_time": time.time(),
    })


@app.get("/api/highlights")
def api_highlights():
    return _json_response(load_highlights())


@app.get("/api/games")
def api_games():
    return _json_response(_sim.game_list() if _sim else [])


@app.post("/api/demo/next")
def api_demo_next():
    if not _sim:
        return JSONResponse({"error": "demo mode is off"}, status_code=400)
    _sim.next_now()
    return _json_response({"ok": True})


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {orjson.dumps(data).decode()}\n\n".encode()


@app.get("/api/stream")
async def api_stream(request: Request):
    q = bus.subscribe()

    async def gen() -> AsyncIterator[bytes]:
        try:
            yield _sse("hello", {
                "mode": "demo" if settings.demo_mode else "live",
                "games": _sim.game_list() if _sim else [],
                "recent": load_highlights()[:30],
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
