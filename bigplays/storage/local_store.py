from __future__ import annotations

from pathlib import Path
from typing import Optional


class LocalStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def write_clip(self, local_path: Path) -> str:
        # Already local; return a file:// URI
        return local_path.absolute().as_uri()

    def write_metadata(self, meta_path: Path) -> Optional[str]:
        return meta_path.absolute().as_uri()

