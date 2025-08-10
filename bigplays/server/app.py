from __future__ import annotations

from pathlib import Path
from typing import List

import orjson
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

from bigplays.config import settings


def _json_response(data):
    return JSONResponse(orjson.dumps(data), media_type="application/json")


app = FastAPI(title="BigPlays")

# Serve clips as static files for playback
app.mount("/static", StaticFiles(directory=str(settings.clips_dir)), name="static")


@app.get("/")
def index() -> HTMLResponse:
    clips_dir = settings.clips_dir
    clips = sorted([p for p in clips_dir.glob("*.mp4")])
    items = []
    for c in clips:
        meta = c.with_suffix(".json")
        title = c.name
        if meta.exists():
            try:
                data = orjson.loads(meta.read_bytes())
                title = data.get("title", title)
            except Exception:
                pass
        items.append((title, c.name))
    html = """
    <html><head><title>BigPlays</title></head>
    <body>
    <h1>Highlights</h1>
    <ul>
    """
    for title, name in items:
        html += f"<li><a href='/clips/{name}'>{title}</a></li>"
    html += """
    </ul>
    </body></html>
    """
    return HTMLResponse(html)


@app.get("/clips/{filename}")
def get_clip(filename: str):
    path = settings.clips_dir / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return HTMLResponse(
        f"""
        <video controls width="720" src="/static/{filename}"></video>
        <p><a href="/static/{filename}">Download</a></p>
        """
    )


@app.get("/api/highlights")
def api_highlights():
    clips_dir = settings.clips_dir
    items = []
    for c in sorted([p for p in clips_dir.glob("*.mp4")]):
        meta = c.with_suffix(".json")
        data = {"file": c.name}
        if meta.exists():
            try:
                data.update(orjson.loads(meta.read_bytes()))
            except Exception:
                pass
        items.append(data)
    return _json_response(items)

