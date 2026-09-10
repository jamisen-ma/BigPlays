from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import typer
import uvicorn
from dotenv import load_dotenv

from bigplays.config import settings
from bigplays.ingest.espn import GameState, League, detect_scoring_events, fetch_scoreboard, parse_games
from bigplays.ingest.ppvto import (
    DEFAULT_API_BASE as PPV_API_BASE,
    PPVStream,
    list_live_streams,
    resolve_hls_url,
)
from bigplays.ingest.mock_social import mock_social_bursts
from bigplays.media.hls_clipper import clip_window
from bigplays.media.stream_buffer import StreamBuffer, StreamBufferConfig
from bigplays.models import HighlightEvent
from bigplays.orchestrator.highlight_detector import detect_highlight
from bigplays.server.app import app as fastapi_app
from bigplays.storage.local_store import LocalStore
from bigplays.storage.s3_store import S3Store
from bigplays.utils.logging import configure_logging, get_logger


cli = typer.Typer(help="BigPlays CLI")


def _ensure_dirs() -> None:
    settings.buffer_dir.mkdir(parents=True, exist_ok=True)
    (settings.buffer_dir / "segments").mkdir(parents=True, exist_ok=True)
    settings.clips_dir.mkdir(parents=True, exist_ok=True)


@cli.command("env")
def show_env() -> None:
    """Print effective configuration."""
    data = {
        "leagues": settings.leagues,
        "espn_poll_seconds": settings.espn_poll_seconds,
        "use_llm": settings.use_llm,
        "buffer_dir": str(settings.buffer_dir),
        "clips_dir": str(settings.clips_dir),
        "enable_s3": settings.enable_s3,
        "s3_bucket": settings.s3_bucket,
        "s3_prefix": settings.s3_prefix,
        "ppv_api_base": PPV_API_BASE,
    }
    typer.echo(json.dumps(data, indent=2))


recorder = typer.Typer(help="Manage HLS recording buffer.")
cli.add_typer(recorder, name="recorder")


@recorder.command("start")
def recorder_start(stream_url: Optional[str] = typer.Option(None, help="HLS stream URL")) -> None:
    load_dotenv()
    configure_logging()
    _ensure_dirs()
    url = stream_url or settings.stream_url
    if not url:
        typer.echo("STREAM_URL required")
        raise typer.Exit(code=2)
    cfg = StreamBufferConfig(stream_url=url, buffer_dir=settings.buffer_dir)
    sb = StreamBuffer(cfg)
    log = get_logger("recorder")
    log.info("starting_recorder", url=url, buffer=str(settings.buffer_dir))
    sb.start()
    try:
        while True:
            time.sleep(10)
            sb.prune_old()
    except KeyboardInterrupt:
        log.info("stopping_recorder")
        sb.stop()


ppv = typer.Typer(help="PPV.to stream utilities.")
cli.add_typer(ppv, name="ppv")


@ppv.command("list")
def ppv_list(category: Optional[str] = typer.Option(None, help="Filter by category name")) -> None:
    from bigplays.ingest.ppvto import fetch_streams

    cats = fetch_streams()
    for c in cats:
        if category and c.category != category:
            continue
        typer.echo(f"[{c.category}] id={c.id} always_live={c.always_live}")
        for s in c.streams:
            live_flag = "LIVE" if s.is_live_now() else "-"
            typer.echo(f"  {live_flag} id={s.id} name={s.name} uri_name={s.uri_name}")


@ppv.command("live")
def ppv_live(category: Optional[str] = typer.Option(None, help="Filter by category name")) -> None:
    cats = [category] if category else None
    streams = list_live_streams(categories=cats)
    for s in streams:
        typer.echo(f"LIVE {s.category_name or ''}: {s.name} uri={s.uri_name}")


@ppv.command("resolve")
def ppv_resolve(uri_name: str = typer.Argument(..., help="uri_name from API")) -> None:
    # Build a temporary stream object to resolve
    st = PPVStream(
        id=0,
        name=uri_name,
        tag="",
        poster=None,
        uri_name=uri_name,
        starts_at=None,
        ends_at=None,
        always_live=False,
        category_name=None,
    )
    hls = resolve_hls_url(st)
    if not hls:
        typer.echo("Could not resolve HLS URL")
        raise typer.Exit(1)
    typer.echo(hls)


@ppv.command("record")
def ppv_record(uri_name: str = typer.Argument(..., help="uri_name from API")) -> None:
    load_dotenv()
    configure_logging()
    _ensure_dirs()
    st = PPVStream(
        id=0,
        name=uri_name,
        tag="",
        poster=None,
        uri_name=uri_name,
        starts_at=None,
        ends_at=None,
        always_live=False,
        category_name=None,
    )
    hls = resolve_hls_url(st)
    if not hls:
        typer.echo("Could not resolve HLS URL from PPV.to page")
        raise typer.Exit(2)
    typer.echo(f"Recording from {hls}")
    cfg = StreamBufferConfig(stream_url=hls, buffer_dir=settings.buffer_dir)
    sb = StreamBuffer(cfg)
    sb.start()
    try:
        while True:
            time.sleep(10)
            sb.prune_old()
    except KeyboardInterrupt:
        sb.stop()


agent = typer.Typer(help="Run monitoring and highlight detection agent.")
cli.add_typer(agent, name="agent")


def _upload_if_enabled(local_path: Path, metadata: dict) -> str:
    if settings.enable_s3 and settings.s3_bucket:
        s3 = S3Store(settings.s3_bucket, settings.s3_prefix)
        uri = s3.upload_file(local_path, extra_metadata={"title": metadata.get("title", "")})
        s3.upload_json(local_path.with_suffix(".json"))
        return uri
    else:
        store = LocalStore(settings.clips_dir)
        return store.write_clip(local_path)


@agent.command("run")
def agent_run(league: Optional[str] = typer.Option(None, help="nba|nfl or omit for both")) -> None:
    load_dotenv()
    configure_logging()
    _ensure_dirs()
    log = get_logger("agent")
    leagues: List[League] = []
    if league:
        leagues = [League(league)]
    else:
        leagues = [League(l) for l in settings.leagues]

    last_states: Dict[str, GameState] = {}
    social_stream = mock_social_bursts()
    next_social = next(social_stream)
    while True:
        try:
            for lg in leagues:
                data = fetch_scoreboard(lg)
                games = parse_games(lg, data)
                now = datetime.now(timezone.utc)
                for g in games:
                    key = f"{g.league.value}:{g.game_id}"
                    prev = last_states.get(key)
                    if prev:
                        events = list(detect_scoring_events(prev, g, occurred_utc=now))
                        for e in events:
                            # catch up social signal timebox
                            social_score = None
                            while next_social and next_social.occurred_utc <= now:
                                social_score = max(social_score or 0.0, next_social.score)
                                try:
                                    next_social = next(social_stream)
                                except StopIteration:
                                    next_social = None
                                    break
                            he = detect_highlight(
                                scoring_event=e,
                                prev_home_score=prev.home_score,
                                prev_away_score=prev.away_score,
                                social_burst_score=social_score,
                                time_left_hint=None,  # Not available from ESPN scoreboard
                            )
                            if he:
                                out_file = settings.clips_dir / f"{he.league.value}_{he.game_id}_{he.event_id}.mp4"
                                metadata = {
                                    "event_id": he.event_id,
                                    "game_id": he.game_id,
                                    "league": he.league.value,
                                    "occurred_utc": he.occurred_utc.isoformat(),
                                    "reasons": [r.value for r in he.reasons],
                                    "base_score": he.base_score,
                                    "combined_score": he.combined_score,
                                    "tags": he.tags,
                                    "title": he.title,
                                }
                                try:
                                    clip_window(
                                        segments_dir=settings.buffer_dir / "segments",
                                        start_utc=he.clip_start_utc,
                                        end_utc=he.clip_end_utc,
                                        out_path=out_file,
                                        metadata=metadata,
                                    )
                                    uri = _upload_if_enabled(out_file, metadata)
                                    log.info("highlight_created", file=str(out_file), uri=uri, meta=metadata)
                                except Exception as ex:
                                    log.info("clip_failed", error=str(ex))
                    last_states[key] = g
            time.sleep(settings.espn_poll_seconds)
        except KeyboardInterrupt:
            break


server = typer.Typer(help="Run API/dashboard server.")
cli.add_typer(server, name="server")


@server.command("run")
def server_run(
    host: Optional[str] = None,
    port: Optional[int] = None,
    demo: bool = typer.Option(False, "--demo", help="Replay scripted plays with synthetic clips (no live games needed)"),
) -> None:
    load_dotenv()
    configure_logging()
    _ensure_dirs()
    if demo:
        settings.demo_mode = True
        if not any(settings.demo_clips_dir.glob("*.mp4")):
            from bigplays.demo.clip_gen import render_all

            typer.echo("No demo clips found; rendering synthetic clips...")
            render_all(settings.demo_clips_dir)
    h = host or settings.server_host
    p = port or settings.server_port
    # short graceful-shutdown window so open SSE streams don't keep a dying server alive
    uvicorn.run(fastapi_app, host=h, port=p, reload=False, timeout_graceful_shutdown=2)


demo = typer.Typer(help="Demo utilities: synthetic clips and scripted live replay.")
cli.add_typer(demo, name="demo")


@demo.command("clips")
def demo_clips(force: bool = typer.Option(False, "--force", help="Re-render even if clips exist")) -> None:
    """Render synthetic highlight clips for every scripted demo play."""
    from bigplays.demo.clip_gen import render_all

    outs = render_all(settings.demo_clips_dir, force=force)
    typer.echo(f"{len(outs)} demo clips in {settings.demo_clips_dir}")


@demo.command("run")
def demo_run(host: Optional[str] = None, port: Optional[int] = None) -> None:
    """Start the server in demo mode (same as `server run --demo`)."""
    server_run(host=host, port=port, demo=True)


assemble = typer.Typer(help="Assemble reels from existing clips.")
cli.add_typer(assemble, name="assemble")


@assemble.command("reels")
def assemble_reels() -> None:
    # Simple demo: build a chronological reel of all current clips
    from bigplays.assembly.reel_builder import build_reel

    clips = sorted(settings.clips_dir.glob("*.mp4"))
    if not clips:
        typer.echo("No clips to assemble")
        raise typer.Exit(0)
    out = settings.clips_dir / "reel.mp4"
    build_reel(clips, out)
    typer.echo(f"Wrote reel to {out}")


if __name__ == "__main__":
    cli()

