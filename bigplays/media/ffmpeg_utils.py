from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


def ensure_ffmpeg_installed() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found on PATH")


def run_ffmpeg(args: List[str], cwd: Optional[Path] = None) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"] + args
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")


def run_ffprobe(args: List[str]) -> subprocess.CompletedProcess:
    cmd = ["ffprobe", "-v", "error"] + args
    return subprocess.run(cmd, capture_output=True, text=True)

