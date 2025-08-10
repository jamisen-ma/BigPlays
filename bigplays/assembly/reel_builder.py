from __future__ import annotations

from pathlib import Path
from typing import List

from bigplays.media.hls_clipper import concat_segments_to_mp4


def build_reel(clips: List[Path], out_path: Path) -> Path:
    if not clips:
        raise ValueError("no clips for reel")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_segments_to_mp4(clips, out_path)
    return out_path

