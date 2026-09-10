from __future__ import annotations

"""Live demo simulator.

Replays the scripted plays as if the real agent were running: for each play it
emits the pipeline stages (ingest -> retrieve -> heuristics -> social -> LLM ->
clip -> store) over a few seconds, then publishes the finished highlight with
its synthetic clip copied into the clips directory with a JSON sidecar, exactly
like the production agent writes them.
"""

import asyncio
import hashlib
import itertools
import json
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from bigplays.demo.plays import PLAYS, DemoPlay
from bigplays.server.events import EventBus


def _clock_seconds(clock: str) -> float:
    try:
        mm, ss = clock.split(":")
        return int(mm) * 60 + float(ss)
    except Exception:
        return 0.0


def _fmt_clock(secs: float, league: str) -> str:
    secs = max(0.0, secs)
    m = int(secs // 60)
    s = secs - m * 60
    if league == "nba" and secs < 60:
        return f"{m}:{s:04.1f}"
    return f"{m}:{int(s):02d}"


class DemoSimulator:
    def __init__(
        self,
        bus: EventBus,
        clips_dir: Path,
        demo_clips_dir: Path,
        min_interval: float = 7.0,
        max_interval: float = 14.0,
        seed: Optional[int] = None,
    ) -> None:
        self.bus = bus
        self.clips_dir = clips_dir
        self.demo_clips_dir = demo_clips_dir
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.rng = random.Random(seed)
        self.games: Dict[str, dict] = {}
        self.seen_files: set[str] = set()
        self.trigger = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._tick_task: Optional[asyncio.Task] = None
        self.highlight_count = 0
        self._init_games()

    # ------------------------------------------------------------------ games
    def _init_games(self) -> None:
        for p in PLAYS:
            if p.game_id in self.games:
                continue
            self.games[p.game_id] = {
                "game_id": p.game_id,
                "league": p.league,
                "away": p.away,
                "home": p.home,
                "away_color": p.away_color,
                "home_color": p.home_color,
                "away_score": p.away_before,
                "home_score": p.home_before,
                "period": p.period,
                "clock": p.clock,
                "clock_seconds": _clock_seconds(p.clock) + self.rng.uniform(20, 90),
                "status": "in",
            }

    def game_list(self) -> List[dict]:
        out = []
        for g in self.games.values():
            gg = {k: v for k, v in g.items() if k != "clock_seconds"}
            gg["clock"] = _fmt_clock(g["clock_seconds"], g["league"])
            out.append(gg)
        return out

    async def _tick_loop(self) -> None:
        while True:
            for g in self.games.values():
                if g["status"] == "in":
                    g["clock_seconds"] = max(0.0, g["clock_seconds"] - 2.0)
                    if g["clock_seconds"] <= 0:
                        # roll the clock so the demo never "ends"
                        g["clock_seconds"] = 12 * 60 if g["league"] == "nba" else 15 * 60
            self.bus.publish("game_tick", {"games": self.game_list()})
            await asyncio.sleep(2.0)

    # --------------------------------------------------------------- pipeline
    async def _stage(self, play: DemoPlay, stage: str, detail: str, seconds: float, data: Optional[dict] = None) -> None:
        self.bus.publish("pipeline", {"play_id": play.play_id, "stage": stage, "status": "running", "detail": detail, "data": data or {}})
        await asyncio.sleep(seconds)
        self.bus.publish("pipeline", {"play_id": play.play_id, "stage": stage, "status": "done", "detail": detail, "data": data or {}})

    async def _run_play(self, play: DemoPlay) -> dict:
        now = datetime.now(timezone.utc)
        event_id = hashlib.sha1(f"{play.play_id}-{now.isoformat()}".encode()).hexdigest()[:12]
        g = self.games[play.game_id]
        g["clock_seconds"] = _clock_seconds(play.clock)
        g["period"] = play.period
        self.bus.publish("game_tick", {"games": self.game_list()})

        delta = f"{play.away} {play.away_before}→{play.away_after} · {play.home} {play.home_before}→{play.home_after}"
        await self._stage(play, "ingest", f"ESPN scoreboard delta · {delta} · {play.period} {play.clock}", 0.5,
                          {"description": play.description})
        await self._stage(play, "retrieve", f"Kafka event → Pinecone top-{len(play.commentary)} commentary matches (cos 0.{self.rng.randint(84, 96)})", 0.8,
                          {"commentary": play.commentary})
        reasons_h = [r for r in play.reasons if r not in ("llm_viral", "social_spike")]
        await self._stage(play, "heuristic", f"{', '.join(reasons_h) or 'no rule fired'} → base {play.base_score:.2f}", 0.35,
                          {"base_score": play.base_score, "reasons": reasons_h})
        await self._stage(play, "social", f"burst {play.social_score:.2f} · {int(play.social_score * 10)}x baseline · {len(play.social)} samples", 0.45,
                          {"social_score": play.social_score, "samples": play.social})
        await self._stage(play, "llm", f"Claude verdict=True hype={play.hype_score:.2f} · {play.title}", 1.6,
                          {"hype_score": play.hype_score, "tags": play.tags, "title": play.title, "rationale": play.rationale})
        await self._stage(play, "clip", "ffmpeg concat (pre-roll 8s / post-roll 6s) → mp4", 1.0)

        # materialize the clip + sidecar exactly like the agent does
        file_name = f"{play.league}_{play.game_id}_{event_id}.mp4"
        src_mp4 = self.demo_clips_dir / f"{play.play_id}.mp4"
        src_jpg = self.demo_clips_dir / f"{play.play_id}.jpg"
        dst_mp4 = self.clips_dir / file_name
        dst_jpg = dst_mp4.with_suffix(".jpg")
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        if src_mp4.exists():
            shutil.copyfile(src_mp4, dst_mp4)
        if src_jpg.exists():
            shutil.copyfile(src_jpg, dst_jpg)
        s3_key = f"highlights/{play.league}/{play.game_id}/{event_id}.mp4"
        record = {
            "event_id": event_id,
            "game_id": play.game_id,
            "league": play.league,
            "occurred_utc": now.isoformat(),
            "clip_start_utc": (now - timedelta(seconds=8)).isoformat(),
            "clip_end_utc": (now + timedelta(seconds=6)).isoformat(),
            "reasons": play.reasons,
            "base_score": play.base_score,
            "combined_score": round(min(1.0, play.base_score * 0.6 + play.hype_score * 0.6), 3),
            "tags": play.tags,
            "title": play.title,
            "description": play.description,
            "player": play.player,
            "team": play.team,
            "away": play.away,
            "home": play.home,
            "away_color": play.away_color,
            "home_color": play.home_color,
            "away_score": play.away_after,
            "home_score": play.home_after,
            "period": play.period,
            "clock": play.clock,
            "kind": play.kind,
            "llm": {
                "verdict": True,
                "hype_score": play.hype_score,
                "tags": play.tags,
                "title": play.title,
                "rationale": play.rationale,
            },
            "social_score": play.social_score,
            "social": play.social,
            "commentary": play.commentary,
            "storage_uri": f"s3://bigplays-highlights/{s3_key}",
            "date": play.date,
            "youtube_id": play.youtube_id,
            "youtube_start": play.youtube_start,
            "youtube_end": play.youtube_end,
            "source": {
                "title": play.source_title,
                "channel": play.source_channel,
                "url": f"https://www.youtube.com/watch?v={play.youtube_id}" if play.youtube_id else None,
            },
            "file": file_name,
            "poster": dst_jpg.name if dst_jpg.exists() else None,
            "demo": True,
        }
        self.seen_files.add(dst_mp4.with_suffix(".json").name)
        dst_mp4.with_suffix(".json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        await self._stage(play, "store", f"s3://bigplays-highlights/{s3_key} · tagged {len(play.tags)} · sidecar written", 0.4,
                          {"storage_uri": record["storage_uri"]})

        # apply the score change to the live game state
        g["away_score"], g["home_score"] = play.away_after, play.home_after
        self.bus.publish("game_tick", {"games": self.game_list()})
        self.highlight_count += 1
        self.bus.publish("highlight", record)
        return record

    async def _loop(self) -> None:
        await asyncio.sleep(2.0)
        order = list(PLAYS)
        self.rng.shuffle(order)
        for play in itertools.cycle(order):
            try:
                await self._run_play(play)
            except Exception as ex:  # keep the demo alive
                self.bus.publish("log", {"level": "error", "msg": f"demo play failed: {ex}"})
            wait = self.rng.uniform(self.min_interval, self.max_interval)
            try:
                await asyncio.wait_for(self.trigger.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            self.trigger.clear()

    # ------------------------------------------------------------------ control
    def purge_previous(self) -> int:
        """Remove clips written by earlier demo runs so the feed starts clean."""
        removed = 0
        for meta in self.clips_dir.glob("*.json"):
            try:
                if json.loads(meta.read_text()).get("demo"):
                    for ext in (".mp4", ".jpg", ".json"):
                        meta.with_suffix(ext).unlink(missing_ok=True)
                    removed += 1
            except Exception:
                continue
        return removed

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        self._tick_task = asyncio.create_task(self._tick_loop())

    def stop(self) -> None:
        for t in (self._task, self._tick_task):
            if t:
                t.cancel()

    def next_now(self) -> None:
        self.trigger.set()
