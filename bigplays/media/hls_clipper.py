from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from bigplays.media.ffmpeg_utils import run_ffmpeg, media_duration_seconds


def _segments_for_window(segments_dir: Path, start_utc: datetime, end_utc: datetime) -> List[Path]:
    files = sorted(segments_dir.glob("*.ts"))
    chosen: List[Path] = []
    for f in files:
        try:
            ts = datetime.strptime(f.stem, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if start_utc <= ts <= end_utc:
            chosen.append(f)
    return chosen


def concat_segments_to_mp4(segments: List[Path], out_path: Path) -> None:
    if not segments:
        raise ValueError("no segments to concatenate")
    temp_list = out_path.parent / f"{out_path.stem}.txt"
    lines = [f"file '{s.absolute()}'\n" for s in segments]
    temp_list.write_text("".join(lines), encoding="utf-8")
    try:
        run_ffmpeg([
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(temp_list),
            "-c",
            "copy",
            str(out_path),
        ])
    finally:
        temp_list.unlink(missing_ok=True)


def write_metadata_sidecar(out_path: Path, metadata: dict) -> None:
    meta_path = out_path.with_suffix(".json")
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def clip_window(
    segments_dir: Path,
    start_utc: datetime,
    end_utc: datetime,
    out_path: Path,
    metadata: dict,
) -> Path:
    segments = _segments_for_window(segments_dir, start_utc, end_utc)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_segments_to_mp4(segments, out_path)
    write_metadata_sidecar(out_path, metadata)
    return out_path


def clip_recent(segments: List[Path], seconds: int, out_path: Path, metadata: dict) -> Path:
    """Cut completed segments by media duration; HLS arrival times can be irregular."""
    chosen: List[Path] = []
    duration = 0.0
    for segment in reversed(segments):
        length = media_duration_seconds(segment)
        if not length or length <= 0:
            raise ValueError('Cannot determine completed segment duration')
        chosen.append(segment)
        duration += length
        if duration >= seconds:
            break
    if duration < seconds:
        raise ValueError('Buffer is still filling')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_segments_to_mp4(list(reversed(chosen)), out_path)
    write_metadata_sidecar(out_path, metadata)
    return out_path


# ---------------------------------------------------------------------------
# Timestamped-archive cutting (live agent). Segments are dicts from the
# timeline index: {'start': utc_seconds, 'duration': s, 'file': name, 'discontinuity': bool}.

COPY_KEYFRAME_SLACK = 3.0  # accept a stream-copy clip starting at most this early (one GOP)


def _manifest(directory: Path, selected: list, out: Path) -> Path:
    # One manifest per output so concurrent cuts in one archive never clobber each other.
    manifest = directory / f'cut-{out.stem}.txt'
    manifest.write_text(''.join(f"file '{(directory / s['file']).resolve()}'\n" for s in selected))
    return manifest


async def _copy_start(directory: Path, selected: list, start: float) -> float | None:
    """UTC start snapped to the last keyframe at/before `start` in the first segment, or None."""
    from bigplays.media.ffmpeg_utils import keyframe_offsets

    first = selected[0]
    offset = max(0.0, start - first['start'])
    keys = await keyframe_offsets(directory / first['file'])
    before = [k for k in keys if k <= offset + 0.02]
    if before and offset - before[-1] <= COPY_KEYFRAME_SLACK:
        return first['start'] + max(0.0, before[-1])
    after = [k for k in keys if k > offset]
    if not before and after and after[0] - offset <= 1.0:
        return first['start'] + after[0]  # segment's first keyframe is marginally late
    return None


async def cut_timeline_window(directory: Path, selected: list, start: float, end: float, out: Path, *,
                              still_approved=None, poster_at: float | None = None,
                              allow_copy: bool = True, timeout: float = 90) -> dict:
    """Cut [start, end] (UTC seconds) from archived segments into `out` (MP4) and `out`.jpg.

    Stream-copy is tried first (sub-second, starts at the preceding keyframe). It
    falls back to an exact re-encode when segments cross a discontinuity, the
    copy fails, or the copied file does not validate. Nothing is left at `out`
    unless the whole cut succeeds and `still_approved()` is still true.
    """
    import time as _time

    from bigplays.media.ffmpeg_utils import ffmpeg_bin, probe_media, run_async

    started = _time.monotonic()
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(directory, selected, out)
    temp = out.with_suffix('.partial.mp4')
    poster_temp = out.with_suffix('.partial.jpg')
    info = {'mode': None, 'clip_start': start, 'clip_end': end, 'poster': None}
    try:
        crosses = any(s.get('discontinuity') for s in selected[1:])
        copy_start = await _copy_start(directory, selected, start) if allow_copy and not crosses else None
        if copy_start is not None:
            code, _ = await run_async([ffmpeg_bin(), '-v', 'error', '-y',
                '-ss', f'{max(0.0, copy_start - selected[0]["start"]):.3f}',
                '-f', 'concat', '-safe', '0', '-i', str(manifest), '-t', f'{end - copy_start:.3f}',
                '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-movflags', '+faststart', '-f', 'mp4',
                str(temp)], timeout)
            probe = probe_media(temp) if not code and temp.exists() else {}
            wanted = end - start
            if (not code and probe.get('video') and probe.get('duration')
                    and wanted * .8 <= probe['duration'] <= wanted + COPY_KEYFRAME_SLACK + 1.5):
                info.update(mode='copy', clip_start=copy_start, duration=probe['duration'])
            else:
                temp.unlink(missing_ok=True)
        if info['mode'] is None:
            code, _ = await run_async([ffmpeg_bin(), '-v', 'error', '-y', '-f', 'concat', '-safe', '0',
                '-i', str(manifest), '-ss', f'{max(0.0, start - selected[0]["start"]):.3f}',
                '-t', f'{end - start:.3f}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                '-c:a', 'aac', '-movflags', '+faststart', str(temp)], timeout)
            if code:
                raise ValueError('FFmpeg could not encode requested play window')
            info.update(mode='encode', duration=end - start)
        if still_approved is not None and not still_approved():
            raise ValueError('Play approval changed during encoding')
        # Poster is best-effort; a missing poster never blocks a saved clip.
        at = max(0.0, (poster_at if poster_at is not None else (start + end) / 2) - info['clip_start'])
        try:
            code, _ = await run_async([ffmpeg_bin(), '-v', 'error', '-y', '-ss', f'{at:.3f}', '-i', str(temp),
                '-frames:v', '1', '-vf', 'scale=640:-2', '-q:v', '4', '-f', 'image2', str(poster_temp)], 30)
            if not code and poster_temp.exists() and poster_temp.stat().st_size > 0:
                poster_temp.replace(out.with_suffix('.jpg'))
                info['poster'] = out.with_suffix('.jpg').name
        except (asyncio.TimeoutError, OSError):
            pass
        temp.replace(out)
        info['cut_seconds'] = round(_time.monotonic() - started, 3)
        return info
    finally:
        manifest.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)
        poster_temp.unlink(missing_ok=True)
