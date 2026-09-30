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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from bigplays.demo.plays import DemoPlay
from bigplays.demo.replay import replay_plays
from bigplays.server.events import EventBus
from bigplays.storage.catalog import catalog_for


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
        league: str = 'all',
        dataset: str = 'highlights',
        database_path: Optional[Path] = None,
    ) -> None:
        self.bus = bus
        self.clips_dir = clips_dir
        self.demo_clips_dir = demo_clips_dir
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.rng = random.Random(seed)
        self.dataset = dataset
        self.catalog = catalog_for(clips_dir, database_path)
        self.archived_plays = sorted(
            [record for record in self.catalog.all(dataset)
             if record.get('imported') and record.get('file')
             and (clips_dir / record['file']).is_file()],
            key=lambda record: record.get('occurred_utc') or record.get('published_utc') or '',
        )
        self.plays = replay_plays(dataset, league)
        if not self.plays:
            raise ValueError(f'No replay plays for {league}')
        self.games: Dict[str, dict] = {}
        self.extra_games = lambda: []
        self.seen_files: set[str] = set()
        self.trigger = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._tick_task: Optional[asyncio.Task] = None
        self.highlight_count = 0
        self._init_games()

    # ------------------------------------------------------------------ games
    def _init_games(self) -> None:
        if self.archived_plays:
            for record in self.archived_plays:
                if record['game_id'] in self.games:
                    continue
                self.games[record['game_id']] = {
                    key: record.get(key) for key in (
                        'game_id', 'league', 'away', 'home', 'away_color', 'home_color',
                        'away_score', 'home_score', 'period', 'clock', 'season', 'week',
                    )
                }
                self.games[record['game_id']].update(
                    clock_seconds=_clock_seconds(record.get('clock', '')),
                    status='in',
                )
            return
        for p in self.plays:
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
                "clock_seconds": _clock_seconds(p.clock),
                "status": "in",
                "season": p.season,
                "week": p.week,
            }

    def game_list(self) -> List[dict]:
        out = []
        for g in self.games.values():
            gg = {k: v for k, v in g.items() if k != "clock_seconds"}
            gg["clock"] = _fmt_clock(g["clock_seconds"], g["league"]) if g.get('clock') else ''
            out.append(gg)
        return out + self.extra_games()

    async def _tick_loop(self) -> None:
        while True:
            # Historical clocks advance only when a verified play arrives.
            self.bus.publish("game_tick", {"games": self.game_list()})
            await asyncio.sleep(2.0)

    # --------------------------------------------------------------- pipeline
    async def _stage(self, play: DemoPlay, stage: str, detail: str, seconds: float, data: Optional[dict] = None) -> None:
        self.bus.publish("pipeline", {"play_id": play.play_id, "stage": stage, "status": "running", "detail": detail, "data": data or {}})
        await asyncio.sleep(seconds)
        self.bus.publish("pipeline", {"play_id": play.play_id, "stage": stage, "status": "done", "detail": detail, "data": data or {}})

    async def _run_play(self, play: DemoPlay) -> dict:
        now = datetime.now(timezone.utc)
        identity = play.play_id if self.dataset != 'highlights' else f'{play.play_id}-{now.isoformat()}'
        event_id = hashlib.sha1(identity.encode()).hexdigest()[:12]
        g = self.games[play.game_id]
        g['away_score'], g['home_score'] = play.away_before, play.home_before
        g["clock_seconds"] = _clock_seconds(play.clock)
        g["period"] = play.period
        self.bus.publish("game_tick", {"games": self.game_list()})

        delta = f"{play.away} {play.away_before}→{play.away_after} · {play.home} {play.home_before}→{play.home_after}"
        await self._stage(play, "ingest", f"Replay · {delta} · {play.period} {play.clock}", 0.5,
                          {"description": play.description})
        await self._stage(play, "retrieve", f"Replay context · {len(play.commentary)} scripted commentary samples", 0.8,
                          {"commentary": play.commentary})
        reasons_h = [r for r in play.reasons if r not in ("llm_viral", "social_spike")]
        await self._stage(play, "heuristic", f"{', '.join(reasons_h) or 'no rule fired'} → base {play.base_score:.2f}", 0.35,
                          {"base_score": play.base_score, "reasons": reasons_h})
        await self._stage(play, "social", f"Simulated burst {play.social_score:.2f} · {len(play.social)} example reactions", 0.45,
                          {"social_score": play.social_score, "samples": play.social})
        await self._stage(play, "llm", f"Demo verdict=True hype={play.hype_score:.2f} · {play.title}", 1.6,
                          {"hype_score": play.hype_score, "tags": play.tags, "title": play.title, "rationale": play.rationale})
        await self._stage(play, "clip", 'Loading verified Week 3 footage' if play.media_kind == 'broadcast'
                          else 'Loading archived highlight and local animation fallback', 1.0)

        # materialize the clip + sidecar exactly like the agent does
        # Reuse media and one sidecar per historical play across replay cycles.
        file_name = f"replay_{play.play_id}.mp4"
        src_mp4 = self.demo_clips_dir / f"{play.play_id}.mp4"
        src_jpg = self.demo_clips_dir / f"{play.play_id}.jpg"
        dst_mp4 = self.clips_dir / file_name
        dst_jpg = dst_mp4.with_suffix(".jpg")
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        if src_mp4.exists() and not dst_mp4.exists():
            shutil.copyfile(src_mp4, dst_mp4)
        if src_jpg.exists() and not dst_jpg.exists():
            shutil.copyfile(src_jpg, dst_jpg)
        record = {
            "event_id": event_id,
            "game_id": play.game_id,
            "league": play.league,
            "occurred_utc": play.occurred_utc,
            "received_utc": now.isoformat(),
            "timestamp_source": 'ESPN play-by-play wallclock' if play.occurred_utc else None,
            "season": play.season,
            "week": play.week,
            "source_play_id": play.source_play_id,
            "replay_dataset": self.dataset,
            "media_kind": play.media_kind,
            "video_start": play.video_start,
            "video_end": play.video_end,
            "clip_duration": (play.video_end - play.video_start) if play.video_end is not None else None,
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
            "storage_uri": str(dst_mp4) if dst_mp4.exists() else None,
            "date": play.date,
            "youtube_id": play.youtube_id,
            "youtube_start": play.youtube_start,
            "youtube_end": play.youtube_end,
            "source": {
                "title": play.source_title,
                "channel": play.source_channel,
                "url": play.source_url or (f"https://www.youtube.com/watch?v={play.youtube_id}" if play.youtube_id else None),
                "play_by_play_url": play.play_by_play_url,
            },
            "file": file_name if dst_mp4.exists() else None,
            "poster": dst_jpg.name if dst_jpg.exists() else None,
            "demo": True,
        }
        self.seen_files.add(dst_mp4.with_suffix(".json").name)
        temporary = dst_mp4.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temporary.replace(dst_mp4.with_suffix('.json'))
        self.catalog.upsert(record)
        await self._stage(play, "store", f"Replay saved locally · tagged {len(play.tags)} · sidecar written", 0.4,
                          {"storage_uri": record["storage_uri"]})

        # apply the score change to the live game state
        g["away_score"], g["home_score"] = play.away_after, play.home_after
        self.bus.publish("game_tick", {"games": self.game_list()})
        self.highlight_count += 1
        self.bus.publish("highlight", record)
        return record

    async def _loop(self) -> None:
        await asyncio.sleep(2.0)
        if self.archived_plays:
            for record in itertools.cycle(self.archived_plays):
                try:
                    await self._run_archived(record)
                except Exception as ex:
                    self.bus.publish('log', {'level': 'error', 'msg': f'archive replay failed: {ex}'})
                wait = max(self.rng.uniform(self.min_interval, self.max_interval),
                           float(record.get('clip_duration') or 0) + 1)
                try:
                    await asyncio.wait_for(self.trigger.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    pass
                self.trigger.clear()
            return
        order = list(self.plays)
        if self.dataset == 'highlights':
            self.rng.shuffle(order)
        else:
            order.sort(key=lambda p: p.occurred_utc)
        for play in itertools.cycle(order):
            try:
                await self._run_play(play)
            except Exception as ex:  # keep the demo alive
                self.bus.publish("log", {"level": "error", "msg": f"demo play failed: {ex}"})
            wait = self.rng.uniform(self.min_interval, self.max_interval)
            if play.video_end is not None:
                wait = max(wait, play.video_end - play.video_start + 1)
            try:
                await asyncio.wait_for(self.trigger.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            self.trigger.clear()

    async def _run_archived(self, saved: dict) -> dict:
        """Replay saved footage while retaining the verified original event time."""
        record = dict(saved)
        record['received_utc'] = datetime.now(timezone.utc).isoformat()
        self.bus.publish('pipeline', {
            'play_id': record['event_id'], 'stage': 'ingest', 'status': 'running',
            'detail': f'Week 3 archive replay · {record["title"]}',
            'data': {'description': record.get('description', '')},
        })
        await asyncio.sleep(.5)
        game = self.games[record['game_id']]
        if record.get('occurred_utc'):
            for key in ('away_score', 'home_score', 'period', 'clock'):
                game[key] = record.get(key)
            game['clock_seconds'] = _clock_seconds(record.get('clock', ''))
        self.catalog.upsert(record)
        self.highlight_count += 1
        self.bus.publish('pipeline', {
            'play_id': record['event_id'], 'stage': 'ingest', 'status': 'done',
            'detail': 'Saved official NFL highlight loaded from the local library', 'data': {},
        })
        self.bus.publish('game_tick', {'games': self.game_list()})
        self.bus.publish('highlight', record)
        return record

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        self._tick_task = asyncio.create_task(self._tick_loop())

    def stop(self) -> None:
        for t in (self._task, self._tick_task):
            if t:
                t.cancel()

    def next_now(self) -> None:
        self.trigger.set()
