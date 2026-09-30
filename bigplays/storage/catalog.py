"""Durable highlight catalog. SQLite stores metadata; video files stay on disk."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


class HighlightCatalog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS highlights (
                    event_id TEXT PRIMARY KEY, dataset TEXT, game_id TEXT,
                    occurred_utc TEXT, received_utc TEXT, payload TEXT NOT NULL,
                    created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS highlights_dataset_time
                    ON highlights(dataset, occurred_utc DESC);
                CREATE TABLE IF NOT EXISTS sidecars (
                    path TEXT PRIMARY KEY, stamp INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS import_runs (
                    import_key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_utc TEXT NOT NULL
                );
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _write(db, record):
        if not isinstance(record, dict) or not record.get('event_id'):
            raise ValueError('A highlight needs an event_id')
        now = datetime.now(timezone.utc).isoformat()
        db.execute('''INSERT INTO highlights
            (event_id, dataset, game_id, occurred_utc, received_utc, payload, created_utc, updated_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET dataset=excluded.dataset,
            game_id=excluded.game_id, occurred_utc=excluded.occurred_utc,
            received_utc=excluded.received_utc, payload=excluded.payload,
            updated_utc=excluded.updated_utc''',
            (record['event_id'], record.get('replay_dataset'), record.get('game_id'),
             record.get('occurred_utc'), record.get('received_utc'),
             json.dumps(record, ensure_ascii=False), now, now))

    def upsert(self, record, sidecar: Path | None = None):
        """Insert or update by event_id (created_utc is preserved on update).

        With `sidecar`, also records that sidecar's current stamp so the next
        migrate_sidecars() does not re-import the identical record.
        """
        with self.connect() as db:
            self._write(db, record)
            if sidecar is not None and Path(sidecar).exists():
                db.execute('INSERT OR REPLACE INTO sidecars VALUES (?, ?)',
                           (str(Path(sidecar).resolve()), Path(sidecar).stat().st_mtime_ns))

    def patch(self, event_id, fields: dict):
        """Transactional read-merge-write of only the given top-level keys. Returns the merged
        record, or None when the event is not in the catalog (patch never creates rows)."""
        if not isinstance(fields, dict):
            raise ValueError('patch fields must be a dict')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM highlights WHERE event_id=?', (event_id,)).fetchone()
            if not row:
                return None
            record = json.loads(row[0]) | dict(fields)
            record['event_id'] = event_id
            self._write(db, record)
            return record

    def get(self, event_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM highlights WHERE event_id=?', (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, dataset=None):
        query = 'SELECT payload FROM highlights'
        params = ()
        if dataset:
            query += ' WHERE dataset=?'
            params = (dataset,)
        query += " ORDER BY COALESCE(received_utc, occurred_utc, '') DESC, event_id"
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(query, params)]

    def migrate_sidecars(self, clips_dir: Path):
        """Incremental import. Missing sidecars never delete database records."""
        changed = []
        with self.connect() as db:
            known = dict(db.execute('SELECT path, stamp FROM sidecars'))
            for path in clips_dir.glob('*.json'):
                try:
                    stamp = path.stat().st_mtime_ns
                    key = str(path.resolve())
                    if known.get(key) == stamp:
                        continue
                    record = json.loads(path.read_text())
                    if not isinstance(record, dict) or not record.get('event_id'):
                        continue
                    record.setdefault('file', path.with_suffix('.mp4').name)
                    record.setdefault('poster', path.with_suffix('.jpg').name if path.with_suffix('.jpg').exists() else None)
                    self._write(db, record)
                    db.execute('INSERT OR REPLACE INTO sidecars VALUES (?, ?)', (key, stamp))
                    changed.append(record)
                except (OSError, ValueError, TypeError):
                    continue
        return changed

    def save_import(self, key, report):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO import_runs VALUES (?, ?, ?)',
                       (key, json.dumps(report), datetime.now(timezone.utc).isoformat()))

    def imports(self):
        with self.connect() as db:
            return {key: json.loads(value) for key, value in db.execute('SELECT import_key, payload FROM import_runs')}

    def backup(self, destination: Path):
        """SQLite's backup API includes committed WAL contents."""
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError('The backup destination must differ from the catalog')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()


def catalog_for(clips_dir: Path, path: Path | None = None):
    return HighlightCatalog(path or clips_dir.with_suffix('.sqlite3'))
