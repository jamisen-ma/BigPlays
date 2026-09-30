from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bigplays.media.ffmpeg_utils import ensure_ffmpeg_installed, ffmpeg_bin


@dataclass
class StreamBufferConfig:
    stream_url: str
    buffer_dir: Path
    segment_seconds: int = 2
    max_hours: int = 6  # rolling retention
    realtime: bool = False  # read the input at native rate (-re); use for VOD/test playlists so timestamps track wall clock


class StreamBuffer:
    def __init__(self, config: StreamBufferConfig) -> None:
        self.config = config
        self.segments_dir = self.config.buffer_dir / "segments"
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        ensure_ffmpeg_installed()
        out_pattern = str(self.segments_dir / "%Y%m%d-%H%M%S.ts")
        args = []
        if self.config.realtime:
            args += ["-re"]
        args += self.input_args()
        args += [
            "-i",
            self.config.stream_url,
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self.config.segment_seconds),
            "-strftime",
            "1",
            out_pattern,
        ]
        env = os.environ.copy()
        env["TZ"] = "UTC"  # segment filenames are parsed as UTC by the clipper; strftime honours TZ
        self.proc = subprocess.Popen([ffmpeg_bin(), "-hide_banner", "-loglevel", "warning"] + args, env=env)
        self._started_at = datetime.now(timezone.utc).timestamp()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
                self.proc.wait(timeout=5)

    def list_segments(self) -> list[Path]:
        return sorted(self.segments_dir.glob("*.ts"))

    def prune_old(self) -> None:
        # Simple retention by hours; relies on timestamped filenames
        horizon = datetime.now(timezone.utc).timestamp() - self.config.max_hours * 3600
        for seg in self.list_segments():
            try:
                ts = datetime.strptime(seg.stem, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc).timestamp()
                if ts < horizon:
                    seg.unlink(missing_ok=True)
            except ValueError:
                continue

    # -- resilience -------------------------------------------------------
    def input_args(self) -> list[str]:
        """HTTP(S) inputs get ffmpeg's own reconnect options (stream errors / dropped TCP)."""
        if self.config.stream_url.startswith(("http://", "https://")):
            return ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                    "-reconnect_delay_max", "10", "-rw_timeout", "15000000"]
        return []

    def last_segment_age(self) -> float | None:
        segments = self.list_segments()
        if not segments:
            return None
        return max(0.0, datetime.now(timezone.utc).timestamp() - segments[-1].stat().st_mtime)

    def ensure_running(self, stall_seconds: float = 30.0) -> bool:
        """Restart ffmpeg if it exited or produced no segment for `stall_seconds`. True when restarted."""
        exited = self.proc is None or self.proc.poll() is not None
        age = self.last_segment_age()
        started = getattr(self, "_started_at", 0.0)
        stalled = (not exited and datetime.now(timezone.utc).timestamp() - started > stall_seconds
                   and (age is None or age > stall_seconds))
        if not (exited or stalled):
            return False
        self.stop()
        self.start()
        return True


# ---------------------------------------------------------------------------
# Live agent buffer: raw PDT-stamped HLS segments relayed by the resolver.

import asyncio  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402

from bigplays.media.timeline import (TimelineArchive, atomic_json, relay_target, select_window,  # noqa: E402
                                     timestamp, unique_segments)

log = logging.getLogger("stream-buffer")

SEGMENT_GIVE_UP_AFTER = 3  # failed fetches of one listed segment before it is treated as missing


def parse_playlist_tolerant(text: str) -> tuple[list[dict], dict]:
    """Like timeline.parse_playlist, but never rejects a whole playlist for one bad entry.

    After a discontinuity without a fresh PROGRAM-DATE-TIME the wall clock is
    extrapolated from EXTINF (the wall timeline keeps running across an ad
    splice even when media timestamps reset); such segments are marked
    `pdt_extrapolated`. Segments before any PDT anchor are skipped.
    """
    cursor = None
    duration = None
    sequence = 0
    discontinuity = False
    extrapolated = False
    result, stats = [], {"unanchored": 0, "extrapolated": 0, "invalid": 0}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("#EXT-X-KEY:", "#EXT-X-MAP:", "#EXT-X-BYTERANGE:")):
            if line.startswith("#EXT-X-KEY:") and "METHOD=NONE" in line:
                continue
            raise ValueError("Timestamp archive currently requires unencrypted MPEG-TS segments")
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.split(":", 1)[1])
        elif line == "#EXT-X-DISCONTINUITY" or line.startswith("#EXT-X-DISCONTINUITY:"):
            discontinuity = True
            extrapolated = cursor is not None
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            try:
                cursor = timestamp(line.split(":", 1)[1])
                extrapolated = False
            except ValueError:
                pass
        elif line.startswith("#EXTINF:"):
            try:
                duration = float(line.split(":", 1)[1].split(",")[0])
            except ValueError:
                duration = None
        elif line and not line.startswith("#"):
            if duration is None or not 0 < duration <= 60:
                stats["invalid"] += 1
                cursor = None  # timing lost; wait for the next PROGRAM-DATE-TIME
            elif cursor is None:
                stats["unanchored"] += 1
            else:
                segment = {"start": cursor, "duration": duration, "sequence": sequence,
                           "discontinuity": discontinuity, "uri": line}
                if extrapolated:
                    segment["pdt_extrapolated"] = True
                    stats["extrapolated"] += 1
                result.append(segment)
            if cursor is not None and duration:
                cursor += duration
            sequence += 1
            duration = None
            discontinuity = False
    return result, stats


class LiveSegmentBuffer(TimelineArchive):
    """TimelineArchive with reconnect-friendly refresh, missing-segment tolerance,
    stall detection, pinned cut windows and wall-clock retention.

    Retention only ever deletes raw `*.ts` files listed in this buffer's own
    index inside its own directory; saved clips live elsewhere and are never touched.
    """

    def __init__(self, directory: Path, base: str, retention_minutes: int = 30, max_bytes: int = 2 * 1024**3,
                 margin_seconds: float = 180.0):
        super().__init__(directory, base, retention_minutes, max_bytes)
        # Margin covers pre-roll plus broadcast delay (video of a play lags its event time).
        self.margin = margin_seconds
        self.failures: dict[str, int] = {}
        self.missing = 0
        self.skipped_unanchored = 0
        self.last_new_segment_at = time.time() if self.segments else 0.0
        self.last_playlist_at = 0.0
        self.retention_shortfall = False
        self.pins: dict[str, tuple[float, float]] = {}
        self.last_segment_error: str | None = None

    # -- health -----------------------------------------------------------
    def stalled(self, now: float | None = None, minimum: float = 30.0) -> bool:
        """No new segment for max(minimum, 4x typical duration) while playlists are being read."""
        if not self.last_new_segment_at:
            return False
        typical = self.segments[-1]["duration"] if self.segments else 6.0
        return (now or time.time()) - self.last_new_segment_at > max(minimum, 4 * typical)

    def covered_seconds(self) -> float:
        if not self.segments:
            return 0.0
        return self.segments[-1]["start"] + self.segments[-1]["duration"] - self.segments[0]["start"]

    def health(self) -> dict:
        return {"missing_segments": self.missing, "unanchored_segments": self.skipped_unanchored,
                "stalled": self.stalled(), "covered_seconds": round(self.covered_seconds(), 1),
                "retention_shortfall": self.retention_shortfall, "last_segment_error": self.last_segment_error,
                "last_new_segment_age": round(time.time() - self.last_new_segment_at, 1)
                if self.last_new_segment_at else None}

    # -- capture ----------------------------------------------------------
    async def refresh(self, client, root: str) -> list[dict]:
        target = self.playlist or root
        for _ in range(4):
            response = await client.get(relay_target(self.base, target))
            response.raise_for_status()
            text = response.text
            if not text.startswith("#EXTM3U"):
                raise ValueError("Expected HLS playlist")
            if "#EXT-X-STREAM-INF:" not in text:
                break
            target = next(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#"))
        else:
            raise ValueError("Too many nested playlists")
        self.playlist = target
        self.last_playlist_at = time.time()
        if not self.last_new_segment_at:
            self.last_new_segment_at = time.time()  # start the stall clock at first contact
        listed, stats = parse_playlist_tolerant(text)
        self.skipped_unanchored += stats["unanchored"]
        known = {s["file"] for s in self.segments}
        added: list[dict] = []
        errors: list[Exception] = []

        def have(segment):
            return any(s.get("sequence") == segment["sequence"] and abs(s["start"] - segment["start"]) < .15
                       for s in self.segments)

        async def download(segment):
            name = f"{round(segment['start'] * 1000)}-{segment['sequence']}.ts"
            if name in known or self.failures.get(name, 0) >= SEGMENT_GIVE_UP_AFTER or have(segment):
                return None
            try:
                response = await client.get(relay_target(self.base, segment["uri"]))
                response.raise_for_status()
                if not response.content or response.content[0] != 0x47:
                    raise ValueError("Expected unwrapped MPEG-TS from resolver")
            except Exception as error:
                self.failures[name] = self.failures.get(name, 0) + 1
                if self.failures[name] == SEGMENT_GIVE_UP_AFTER:
                    self.missing += 1  # leave a gap; select_window refuses to cut across it
                    log.warning("Segment %s missing after %d attempts", segment["sequence"], SEGMENT_GIVE_UP_AFTER)
                raise error
            path = self.directory / name
            part = path.with_suffix(".part")
            part.write_bytes(response.content)
            part.replace(path)
            self.failures.pop(name, None)
            return {k: v for k, v in segment.items() if k != "uri"} | {"file": name, "bytes": len(response.content)}

        for offset in range(0, len(listed), 4):
            results = await asyncio.gather(*(download(s) for s in listed[offset:offset + 4]), return_exceptions=True)
            fresh = [r for r in results if isinstance(r, dict)]
            errors.extend(r for r in results if isinstance(r, BaseException))
            if fresh:
                self.segments = unique_segments(self.segments + fresh)
                added.extend(fresh)
                atomic_json(self.directory / "index.json", self.segments)
        if added:
            self.last_new_segment_at = time.time()
        self.prune()
        # Keep old failure counters bounded to what the playlist still lists.
        listed_names = {f"{round(s['start'] * 1000)}-{s['sequence']}.ts" for s in listed}
        self.failures = {k: v for k, v in self.failures.items() if k in listed_names}
        self.last_segment_error = type(errors[0]).__name__ if errors else None
        if len(errors) >= 2 and not added:
            # Several distinct segments failing and nothing progressed is systemic (expired
            # signature, dead relay): surface it so the capture loop reconnects. A single
            # stubborn segment is tolerated as a gap; stall detection covers the rest.
            raise errors[0]
        return added

    # -- retention --------------------------------------------------------
    def pin(self, key: str, start: float, end: float) -> None:
        self.pins[key] = (start, end)

    def unpin(self, key: str) -> None:
        self.pins.pop(key, None)

    def _pinned(self, segment) -> bool:
        return any(segment["start"] < end and segment["start"] + segment["duration"] > start
                   for start, end in self.pins.values())

    def _delete(self, segment) -> None:
        path = (self.directory / segment["file"]).resolve()
        if path.suffix != ".ts" or path.parent != self.directory.resolve():
            log.error("Refusing to delete non-buffer file %s", path)
            return
        path.unlink(missing_ok=True)

    def prune(self, now: float | None = None) -> list[dict]:
        """Drop raw segments older than retention + margin, then enforce the byte cap."""
        if not self.segments:
            return []
        newest = self.segments[-1]["start"] + self.segments[-1]["duration"]
        horizon = max(newest, now or 0) - self.retention - self.margin
        expired = [s for s in self.segments if s["start"] + s["duration"] < horizon and not self._pinned(s)]
        keep = [s for s in self.segments if s not in expired]
        total = sum(s.get("bytes", 0) for s in keep)
        self.retention_shortfall = False
        index = 0
        while total > self.max_bytes and len(keep) - index > 1:
            candidate = keep[index]
            if self._pinned(candidate):
                index += 1
                continue
            keep.pop(index)
            expired.append(candidate)
            total -= candidate.get("bytes", 0)
            if candidate["start"] + candidate["duration"] >= newest - self.retention:
                self.retention_shortfall = True  # byte cap is shorter than requested retention
        for segment in expired:
            self._delete(segment)
        if expired:
            self.segments = keep
            atomic_json(self.directory / "index.json", self.segments)
        return expired

    # -- cutting ----------------------------------------------------------
    async def cut(self, start: float, end: float, out: Path, metadata: dict, *, still_approved=None,
                  poster_at: float | None = None) -> dict:
        from bigplays.media.hls_clipper import cut_timeline_window

        selected = select_window(self.segments, start, end)
        if poster_at is None:
            try:  # poster at the aligned play moment when the caller recorded one
                poster_at = timestamp(metadata["alignment"]["video_time_utc"])
            except (KeyError, TypeError, ValueError):
                poster_at = None
            if poster_at is not None and not start <= poster_at <= end:
                poster_at = None
        key = out.stem
        self.pin(key, start, end)
        try:
            info = await cut_timeline_window(self.directory, selected, start, end, out,
                                             still_approved=still_approved, poster_at=poster_at)
        finally:
            self.unpin(key)
        return info
