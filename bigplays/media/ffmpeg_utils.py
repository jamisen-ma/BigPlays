from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


def ffmpeg_bin() -> str:
    """Path to an ffmpeg binary: system one if on PATH, else the one bundled with imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as ex:  # pragma: no cover
        raise RuntimeError("ffmpeg not found on PATH and imageio-ffmpeg is not installed") from ex


def ensure_ffmpeg_installed() -> None:
    ffmpeg_bin()  # raises if nothing usable


def run_ffmpeg(args: List[str], cwd: Optional[Path] = None) -> None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"] + args
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")


def run_ffprobe(args: List[str]) -> subprocess.CompletedProcess:
    probe = shutil.which("ffprobe")
    if probe is None:
        raise RuntimeError("ffprobe not found on PATH")
    return subprocess.run([probe, "-v", "error"] + args, capture_output=True, text=True)


def media_duration_seconds(path: Path) -> Optional[float]:
    """Duration via `ffmpeg -i` parsing, so it works without ffprobe."""
    import re

    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)
