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

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()

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

