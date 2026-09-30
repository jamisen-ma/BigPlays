from __future__ import annotations

import asyncio
import json
import os
import re
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


def ffprobe_bin() -> Optional[str]:
    """ffprobe from FFPROBE_BIN or PATH. Optional: callers fall back to parsing `ffmpeg -i`."""
    configured = os.environ.get("FFPROBE_BIN")
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("ffprobe")


def ensure_ffmpeg_installed() -> None:
    ffmpeg_bin()  # raises if nothing usable


def run_ffmpeg(args: List[str], cwd: Optional[Path] = None) -> None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"] + args
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")


def run_ffprobe(args: List[str]) -> subprocess.CompletedProcess:
    probe = ffprobe_bin()
    if probe is None:
        raise RuntimeError("ffprobe not found on PATH")
    return subprocess.run([probe, "-v", "error"] + args, capture_output=True, text=True)


def media_duration_seconds(path: Path) -> Optional[float]:
    """Duration via `ffmpeg -i` parsing, so it works without ffprobe."""
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def probe_media(path: Path) -> dict:
    """Container duration and stream summary. Uses ffprobe when present, else `ffmpeg -i`.

    Returns {'duration': float|None, 'video': codec|None, 'audio': codec|None,
    'width': int|None, 'height': int|None}. Never raises for unreadable media.
    """
    result = {'duration': None, 'video': None, 'audio': None, 'width': None, 'height': None}
    probe = ffprobe_bin()
    if probe:
        proc = subprocess.run([probe, '-v', 'error', '-show_entries',
                               'format=duration:stream=codec_type,codec_name,width,height',
                               '-of', 'json', str(path)], capture_output=True, text=True)
        try:
            data = json.loads(proc.stdout or '{}')
        except ValueError:
            data = {}
        try:
            result['duration'] = float(data.get('format', {}).get('duration'))
        except (TypeError, ValueError):
            pass
        for stream in data.get('streams', []):
            kind = stream.get('codec_type')
            if kind in ('video', 'audio') and not result[kind]:
                result[kind] = stream.get('codec_name')
                if kind == 'video':
                    result['width'], result['height'] = stream.get('width'), stream.get('height')
        return result
    proc = subprocess.run([ffmpeg_bin(), '-hide_banner', '-i', str(path)], capture_output=True, text=True)
    text = proc.stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", text)
    if m:
        h, mi, s = m.groups()
        result['duration'] = int(h) * 3600 + int(mi) * 60 + float(s)
    video = re.search(r"Stream #.*?Video: (\w+).*?, (\d{2,5})x(\d{2,5})", text)
    if video:
        result['video'], result['width'], result['height'] = video[1], int(video[2]), int(video[3])
    audio = re.search(r"Stream #.*?Audio: (\w+)", text)
    if audio:
        result['audio'] = audio[1]
    return result


def decodes_cleanly(path: Path, timeout: float = 120) -> bool:
    """Full decode pass; True when ffmpeg reports no errors."""
    try:
        proc = subprocess.run([ffmpeg_bin(), '-v', 'error', '-i', str(path), '-f', 'null', '-'],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0 and not proc.stderr.strip()


async def run_async(args: List[str], timeout: float = 90) -> tuple[int, bytes]:
    """Run a media subprocess without blocking the event loop; kill it on timeout/cancel."""
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    return process.returncode or 0, stderr or b''


async def keyframe_offsets(path: Path, timeout: float = 15) -> list[float]:
    """Video keyframe times in seconds from the start of one media file (decodes keyframes only)."""
    def probe():
        try:
            proc = subprocess.run([ffmpeg_bin(), '-hide_banner', '-skip_frame', 'nokey', '-i', str(path),
                                   '-an', '-vf', 'showinfo', '-f', 'null', '-'],
                                  capture_output=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            return []
        if proc.returncode:
            return []
        return sorted(float(v) for v in re.findall(rb'pts_time:(-?[0-9.]+)', proc.stderr))

    return await asyncio.to_thread(probe)
