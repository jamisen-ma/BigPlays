from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bigplays.storage.catalog import HighlightCatalog, catalog_for


class LocalStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def write_clip(self, local_path: Path) -> str:
        # Already local; return a file:// URI
        return local_path.absolute().as_uri()

    def write_metadata(self, meta_path: Path) -> Optional[str]:
        return meta_path.absolute().as_uri()


def _atomic_json(path: Path, value) -> None:
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, path)


class ClipLibrary:
    """Permanent clip library: MP4 + poster + JSON sidecar in clips_dir, row in SQLite.

    `event_id` is stable (league + game id + play id), so a re-detected play is
    recognised as already saved and is never cut or stored twice. Nothing here
    ever deletes a clip file or a catalog row.
    """

    def __init__(self, clips_dir: Path, database_path: Path | None = None,
                 catalog: HighlightCatalog | None = None):
        self.clips_dir = Path(clips_dir)
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog or catalog_for(self.clips_dir, database_path)

    def paths(self, event_id: str) -> dict:
        base = self.clips_dir / event_id
        return {'mp4': base.with_suffix('.mp4'), 'json': base.with_suffix('.json'), 'jpg': base.with_suffix('.jpg')}

    def saved(self, event_id: str) -> bool:
        """True when a playable clip for this event is already in the library (file or row)."""
        paths = self.paths(event_id)
        if paths['json'].exists():
            return True
        row = self.catalog.get(event_id)
        return bool(row and row.get('file') and (self.clips_dir / row['file']).exists())

    def persist(self, record: dict, *, new: bool = True) -> dict:
        """Write the sidecar and upsert the row. Preserves the first capture timestamps on re-save.

        `new=False` is a metadata correction of an existing clip: it never invents capture times.
        """
        event_id = record['event_id']
        paths = self.paths(event_id)
        existing = self.catalog.get(event_id) or {}
        try:  # the sidecar on disk may carry newer post-publication enrichment
            on_disk = json.loads(paths['json'].read_text()) if paths['json'].exists() else {}
        except (OSError, ValueError):
            on_disk = {}
        record = dict(record)
        for source in (on_disk if isinstance(on_disk, dict) else {}, existing):  # disk is freshest
            for key, value in source.items():
                # Social enrichment is written by another process after publication; never clobber it.
                if key.startswith('social_') and key not in record:
                    record[key] = value
        now = datetime.now(timezone.utc).isoformat()
        for key in ('captured_utc', 'received_utc'):
            if existing.get(key):
                record[key] = existing[key]  # re-save must not reorder or re-date the library
            if new:
                record.setdefault(key, now)
        record.setdefault('file', paths['mp4'].name)
        if paths['jpg'].exists():
            record.setdefault('poster', paths['jpg'].name)
        _atomic_json(paths['json'], record)
        self.catalog.upsert(record, sidecar=paths['json'])
        return record
