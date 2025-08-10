from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from bigplays.media.ffmpeg_utils import run_ffmpeg


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

