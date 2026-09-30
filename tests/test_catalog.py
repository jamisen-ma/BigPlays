import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

import pytest

from bigplays.storage.catalog import HighlightCatalog, catalog_for


def highlight(event_id='play-1', **overrides):
    return {
        'event_id': event_id,
        'replay_dataset': 'nfl-2026-week3',
        'game_id': 'game-1',
        'occurred_utc': '2026-09-29T01:23:45Z',
        'received_utc': '2026-09-29T12:00:00Z',
        'file': f'{event_id}.mp4',
        **overrides,
    }


def test_catalog_survives_process_restart_without_sidecars(tmp_path):
    clips = tmp_path / 'clips'
    clips.mkdir()
    record = highlight()
    video = clips / record['file']
    video.write_bytes(b'persisted video')
    sidecar = clips / 'play-1.json'
    sidecar.write_text(json.dumps(record))
    catalog = catalog_for(clips)
    assert catalog.path == tmp_path / 'clips.sqlite3'
    assert catalog.migrate_sidecars(clips) == [{**record, 'poster': None}]
    sidecar.unlink()

    result = subprocess.run(
        [sys.executable, '-c',
         'import json,sys; from pathlib import Path; '
         'from bigplays.storage.catalog import HighlightCatalog; '
         'print(json.dumps(HighlightCatalog(Path(sys.argv[1])).all()))',
         str(catalog.path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == [{**record, 'poster': None}]
    assert video.read_bytes() == b'persisted video'


def test_sidecar_migration_is_incremental_and_accepts_atomic_replacements(tmp_path):
    clips = tmp_path / 'clips'
    clips.mkdir()
    sidecar = clips / 'play-1.json'
    sidecar.write_text(json.dumps(highlight(title='First title')))
    catalog = catalog_for(clips)
    assert len(catalog.migrate_sidecars(clips)) == 1
    assert catalog.migrate_sidecars(clips) == []
    with catalog.connect() as db:
        created = db.execute('SELECT created_utc FROM highlights').fetchone()[0]

    previous_stamp = sidecar.stat().st_mtime_ns
    replacement = clips / 'pending.json.tmp'
    replacement.write_text(json.dumps(highlight(title='Corrected title')))
    os.utime(replacement, ns=(previous_stamp + 1, previous_stamp + 1))
    replacement.replace(sidecar)
    assert len(catalog.migrate_sidecars(clips)) == 1
    assert catalog.all()[0]['title'] == 'Corrected title'
    with catalog.connect() as db:
        assert db.execute('SELECT created_utc FROM highlights').fetchone()[0] == created
        assert db.execute('SELECT COUNT(*) FROM sidecars').fetchone()[0] == 1


def test_invalid_sidecars_do_not_prevent_valid_imports_or_recovery(tmp_path):
    (tmp_path / 'broken.json').write_text('{')
    (tmp_path / 'array.json').write_text('[]')
    (tmp_path / 'missing-id.json').write_text('{"title":"No event ID"}')
    (tmp_path / 'good.json').write_text(json.dumps(highlight()))
    (tmp_path / 'good.jpg').write_bytes(b'poster')
    catalog = HighlightCatalog(tmp_path / 'catalog.sqlite3')
    assert len(catalog.migrate_sidecars(tmp_path)) == 1
    assert catalog.all()[0]['poster'] == 'good.jpg'
    (tmp_path / 'broken.json').write_text(json.dumps(highlight('repaired')))
    assert len(catalog.migrate_sidecars(tmp_path)) == 1
    assert len(catalog.all()) == 2
    with pytest.raises(ValueError, match='event_id'):
        catalog.upsert({'title': 'No event ID'})


def test_api_keeps_metadata_when_video_is_missing_and_restores_when_returned(monkeypatch, tmp_path):
    from bigplays.server.app import load_highlights, settings

    clips = tmp_path / 'clips'
    clips.mkdir()
    catalog = catalog_for(clips)
    record = highlight()
    catalog.upsert(record)
    monkeypatch.setattr(settings, 'clips_dir', clips)
    monkeypatch.setattr(settings, 'database_path', catalog.path)
    monkeypatch.setattr(settings, 'demo_mode', False)
    assert load_highlights() == [{**record, 'file': None}]
    assert catalog.all() == [record]
    (clips / record['file']).write_bytes(b'restored video')
    assert load_highlights() == [record]


def test_entire_catalog_and_dataset_filter_are_unlimited(monkeypatch, tmp_path):
    from bigplays.server.app import load_highlights, settings

    catalog = HighlightCatalog(tmp_path / 'catalog.sqlite3')
    for index in range(350):
        catalog.upsert(highlight(f'play-{index:03}'))
    catalog.upsert(highlight('other-week', replay_dataset='nfl-2026-week2'))
    assert len(catalog.all()) == 351
    assert len(catalog.all('nfl-2026-week3')) == 350
    assert catalog.all('missing') == []
    monkeypatch.setattr(settings, 'clips_dir', tmp_path)
    monkeypatch.setattr(settings, 'database_path', catalog.path)
    monkeypatch.setattr(settings, 'demo_mode', True)
    monkeypatch.setattr(settings, 'demo_dataset', 'nfl-2026-week3')
    assert len(load_highlights()) == 350


def test_parallel_imports_are_idempotent(tmp_path):
    catalog = HighlightCatalog(tmp_path / 'catalog.sqlite3')
    records = [highlight(f'play-{index}') for index in range(48)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(catalog.upsert, records * 2))
    assert {row['event_id'] for row in catalog.all()} == {row['event_id'] for row in records}


def test_backup_includes_committed_wal_and_import_reports(tmp_path):
    catalog = HighlightCatalog(tmp_path / 'catalog.sqlite3')
    report = {'discovered': 350, 'imported': 350, 'failed': []}
    with catalog.connect() as active:
        active.execute('PRAGMA wal_autocheckpoint=0')
        catalog.upsert(highlight())
        catalog.save_import('nfl-2026-week3', report)
        destination = tmp_path / 'backups' / 'saved.sqlite3'
        catalog.backup(destination)
    restored = HighlightCatalog(destination)
    assert restored.all() == [highlight()]
    assert restored.imports() == {'nfl-2026-week3': report}
    with pytest.raises(ValueError, match='destination'):
        catalog.backup(catalog.path)


def test_custom_database_path(tmp_path):
    location = tmp_path / 'database' / 'highlights.sqlite3'
    catalog = catalog_for(tmp_path / 'media', location)
    catalog.upsert(highlight())
    assert location.is_file()
    assert HighlightCatalog(location).all() == [highlight()]
