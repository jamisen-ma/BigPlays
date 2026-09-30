"""Continuous buffer, prompt cut, and permanent persistence of live plays."""
import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from bigplays.config import settings
from bigplays.ingest import plays as play_feed
from bigplays.media.ffmpeg_utils import decodes_cleanly, probe_media
from bigplays.media.stream_buffer import LiveSegmentBuffer, parse_playlist_tolerant
from bigplays.media.timeline import select_window, timestamp
from bigplays.orchestrator import live_agent
from bigplays.orchestrator.live_agent import DISCOVERY_EXCLUDED_LEAGUES, GameMonitor, clip_id, mlb_alignment
from bigplays.storage.catalog import HighlightCatalog
from bigplays.storage.local_store import ClipLibrary

ROOT = Path(__file__).resolve().parents[1]
MLB = ROOT / 'data' / 'validation' / 'mlb' / '401907896'
LIVE_DB = ROOT / 'data' / 'clips.sqlite3'
needs_mlb = pytest.mark.skipif(not (MLB / 'index.json').exists(), reason='saved MLB TS buffer not present')


def mlb_game():
    return {'league': 'mlb', 'game_id': '401907896', 'name': 'Chicago White Sox at Houston Astros',
            'url': 'https://example.invalid/stream'}


def load_mlb_buffer(agent_dir):
    target = agent_dir / 'mlb-401907896'
    target.mkdir(parents=True)
    for path in MLB.glob('*.ts'):
        shutil.copy(path, target / path.name)
    shutil.copy(MLB / 'index.json', target / 'index.json')
    return json.loads((MLB / 'index.json').read_text())


@pytest.fixture
def temp_library(monkeypatch, tmp_path):
    """Settings pointed at temp dirs; the SQLite DB is a TEMP copy of the live library when present."""
    db = tmp_path / 'library.sqlite3'
    if LIVE_DB.exists():
        source = sqlite3.connect(f'file:{LIVE_DB}?mode=ro', uri=True)  # read-only; never writes the live DB
        target = sqlite3.connect(db)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    for name, value in [('agent_dir', tmp_path / 'agent'), ('clips_dir', tmp_path / 'clips'),
                        ('database_path', db), ('social_clip_gate', False)]:
        monkeypatch.setattr(settings, name, value)
    return db


def row_count(db):
    with sqlite3.connect(db) as conn:
        return conn.execute('SELECT COUNT(*) FROM highlights').fetchone()[0]


@needs_mlb
def test_mlb_seam_cuts_real_ts_buffer_once_and_survives_restart(monkeypatch, temp_library, tmp_path):
    segments = load_mlb_buffer(settings.agent_dir)
    first = segments[0]['start']
    play = {'play_id': '4019078960001', 'occurred': first - 20, 'period': 7, 'inning': 7, 'inning_half': 'Bottom',
            'period_label': 'Bottom 7', 'clock': '', 'clock_seconds': None, 'count': '2-1', 'outs': 1,
            'text': 'Alvarez homered to right', 'interesting': True, 'home_score': 4, 'away_score': 2,
            'event_type': 'home-run'}
    calls = []

    def locate_mlb_play(play_, observations):  # the timing agent's contract: a clip window or None
        calls.append(play_['play_id'])
        return {'start': first + 2.5, 'end': first + 14.0, 'time': first + 9.0, 'inning': 7}

    monkeypatch.setattr(play_feed, 'locate_mlb_play', locate_mlb_play, raising=False)
    before = row_count(temp_library)
    event_id = clip_id(mlb_game(), play['play_id'])
    out = settings.clips_dir / (event_id + '.mp4')

    async def run_once():
        monitor = GameMonitor(mlb_game())
        assert monitor.alignment is not None, 'MLB seam must dispatch to the timing hooks when present'
        assert len(monitor.archive.segments) == 4  # buffer index recovered from disk
        monitor.plays = [play]
        assert monitor.play_status(play) in ('ready to clip', 'clipped')
        await monitor.clip_play(play)
        return monitor

    monitor = asyncio.run(run_once())
    assert out.exists() and out.with_suffix('.jpg').exists()
    record = json.loads(out.with_suffix('.json').read_text())
    assert record['event_id'] == event_id and record['league'] == 'mlb'
    assert record['event_time_utc'] == record['occurred_utc']
    assert timestamp(record['captured_utc']) > timestamp(record['occurred_utc'])
    assert record['capture']['cut_mode'] == 'copy'
    assert record['capture']['event_to_clip_seconds'] > 0
    assert record['poster'] == event_id + '.jpg' and record['inning_half'] == 'Bottom'
    assert monitor.play_status(play) == 'clipped'
    assert row_count(temp_library) == before + 1

    probe = probe_media(out)
    assert probe['video'] == 'h264' and probe['audio'] == 'aac'
    assert (probe['width'], probe['height']) == (1920, 1080)
    assert 11.0 <= probe['duration'] <= 14.5  # copy starts at the preceding keyframe (<= one 2s GOP)
    assert decodes_cleanly(out)

    # Restart: a fresh process reads the row, and re-detection does not cut again,
    # even when the sidecar is gone (the SQLite row is the durable record).
    listed = subprocess.run([sys.executable, '-c',
        'import json,sys; from pathlib import Path; from bigplays.storage.catalog import HighlightCatalog; '
        'print(json.dumps(HighlightCatalog(Path(sys.argv[1])).get(sys.argv[2])))',
        str(temp_library), event_id], capture_output=True, text=True, check=True, cwd=ROOT)
    assert json.loads(listed.stdout)['capture']['cut_mode'] == 'copy'
    out.with_suffix('.json').unlink()
    mtime = out.stat().st_mtime_ns
    calls.clear()
    asyncio.run(run_once())
    asyncio.run(run_once())
    assert out.stat().st_mtime_ns == mtime and not calls
    assert row_count(temp_library) == before + 1
    assert live_agent.settings.database_path != LIVE_DB


@needs_mlb
def test_cut_across_discontinuity_reencodes_exact_window(tmp_path):
    segments = load_mlb_buffer(tmp_path)
    archive = LiveSegmentBuffer(tmp_path / 'mlb-401907896', 'http://127.0.0.1:3000')
    archive.segments[1]['discontinuity'] = True
    start = segments[0]['start'] + 3
    out = tmp_path / 'clips' / 'disc.mp4'
    info = asyncio.run(archive.cut(start, start + 3, out, {}))
    assert info['mode'] == 'encode' and info['clip_start'] == start
    assert abs(probe_media(out)['duration'] - 3) < .3
    assert not archive.pins  # the retention pin is released after the cut


def test_playlist_parser_tolerates_discontinuity_and_unanchored_entries():
    text = '\n'.join(['#EXTM3U', '#EXT-X-MEDIA-SEQUENCE:10', '#EXTINF:4.0,', '/api/hls?early',
                      '#EXT-X-PROGRAM-DATE-TIME:2026-09-29T20:00:00Z', '#EXTINF:4.0,', '/api/hls?a',
                      '#EXT-X-DISCONTINUITY', '#EXTINF:4.0,', '/api/hls?b',
                      '#EXT-X-PROGRAM-DATE-TIME:2026-09-29T20:00:10Z', '#EXTINF:4.0,', '/api/hls?c'])
    segments, stats = parse_playlist_tolerant(text)
    assert [s['sequence'] for s in segments] == [11, 12, 13] and stats['unanchored'] == 1
    base = timestamp('2026-09-29T20:00:00Z')
    assert segments[1]['start'] == base + 4 and segments[1]['pdt_extrapolated'] and segments[1]['discontinuity']
    assert segments[2]['start'] == base + 10 and 'pdt_extrapolated' not in segments[2]


class FakeClient:
    """Resolver relay stub: one playlist; a chosen segment always 404s."""

    def __init__(self, playlist, broken=()):
        self.playlist, self.broken, self.fetches = playlist, set(broken), {}

    async def get(self, url):
        request = httpx.Request('GET', url)
        self.fetches[url] = self.fetches.get(url, 0) + 1
        if url.endswith('/api/hls?playlist'):
            return httpx.Response(200, text=self.playlist, request=request)
        if any(url.endswith(b) for b in self.broken):
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=b'\x47' + b'\x00' * 187, request=request)


def test_missing_segment_leaves_gap_without_stalling_capture(tmp_path):
    lines = ['#EXTM3U', '#EXT-X-MEDIA-SEQUENCE:1', '#EXT-X-PROGRAM-DATE-TIME:2026-09-29T20:00:00Z']
    for n in range(1, 6):
        lines += ['#EXTINF:4.0,', f'/api/hls?seg{n}']
    client = FakeClient('\n'.join(lines), broken=['seg3'])
    archive = LiveSegmentBuffer(tmp_path / 'buf', 'http://127.0.0.1:3000')
    for _ in range(4):
        asyncio.run(archive.refresh(client, '/api/hls?playlist'))  # never raises: other segments progressed/known
    assert [s['sequence'] for s in archive.segments] == [1, 2, 4, 5]
    assert archive.missing == 1 and client.fetches['http://127.0.0.1:3000/api/hls?seg3'] == 3
    base = timestamp('2026-09-29T20:00:00Z')
    select_window(archive.segments, base + 1, base + 7)  # before the gap: cuttable
    with pytest.raises(ValueError, match='missing section'):
        select_window(archive.segments, base + 6, base + 14)  # never splice across missing footage


def test_retention_trims_raw_segments_only_and_keeps_recent_and_pinned(tmp_path):
    buffer_dir = tmp_path / 'agent' / 'nfl-1'
    clips = tmp_path / 'clips'
    clips.mkdir(parents=True)
    (clips / 'saved.mp4').write_bytes(b'clip')
    archive = LiveSegmentBuffer(buffer_dir, 'http://127.0.0.1:3000', retention_minutes=5, max_bytes=10**9,
                                margin_seconds=60)
    now = 100_000.0
    archive.segments = []
    for n in range(120):  # 20 minutes of 10s segments, oldest first
        name = f'{n}.ts'
        (buffer_dir / name).write_bytes(b'\x47' * 10)
        archive.segments.append({'start': now - 1200 + n * 10, 'duration': 10, 'sequence': n, 'file': name, 'bytes': 10})
    archive.pin('old-play', now - 1195, now - 1185)
    archive.prune(now)
    horizon = now - 300 - 60
    kept = archive.segments
    assert [s['file'] for s in kept if s['start'] + s['duration'] < horizon] == ['0.ts', '1.ts']  # pinned only
    # A play whose event was N <= retention seconds ago (plus pre-roll) is still fully buffered.
    select_window(kept, now - 300 - 12, now - 300 + 4)
    assert (clips / 'saved.mp4').read_bytes() == b'clip'
    assert not (buffer_dir / '50.ts').exists() and (buffer_dir / '119.ts').exists()
    # A byte cap shorter than retention is reported, never silently accepted.
    archive.unpin('old-play')
    archive.max_bytes = 50
    archive.prune(now)
    assert len(archive.segments) == 5 and archive.retention_shortfall
    # Only this buffer's own *.ts files are ever deleted.
    archive.segments.insert(0, {'start': 0, 'duration': 1, 'file': '../../clips/saved.mp4', 'bytes': 0})
    archive.max_bytes = 10**9
    archive.prune(now)
    assert (clips / 'saved.mp4').exists()


def test_stall_detection_uses_segment_progress(tmp_path):
    archive = LiveSegmentBuffer(tmp_path / 'buf', 'http://127.0.0.1:3000')
    assert not archive.stalled()  # never contacted
    archive.last_new_segment_at = 1000
    archive.segments = [{'start': 0, 'duration': 6, 'file': 'x.ts'}]
    assert not archive.stalled(now=1020, minimum=30)
    assert archive.stalled(now=1031, minimum=30)


def test_mlb_discovery_enabled_with_real_alignment_hooks(temp_library):
    assert 'mlb' not in DISCOVERY_EXCLUDED_LEAGUES
    assert GameMonitor(mlb_game()).alignment is not None


def test_mlb_is_skipped_safely_until_timing_hooks_exist(monkeypatch, temp_library):
    monkeypatch.delattr(play_feed, 'locate_mlb_play', raising=False)
    if 'league' not in __import__('inspect').signature(play_feed.locate_play).parameters:
        assert mlb_alignment() is None
        monitor = GameMonitor(mlb_game())
        monitor.archive.segments = [{'start': 0, 'duration': 4, 'file': 'x.ts'}]
        play = {'play_id': '1', 'occurred': 10, 'interesting': True}
        assert monitor.play_status(play) == 'waiting for MLB video alignment support'
        assert monitor.locate(play) is None
    football = GameMonitor({'league': 'nfl', 'game_id': '9', 'name': 'x', 'url': 'u'})
    assert football.alignment is not None


def test_persist_is_idempotent_and_preserves_enrichment(temp_library):
    library = ClipLibrary(settings.clips_dir, settings.database_path)
    before = row_count(temp_library)
    record = {'event_id': 'live-test-1', 'league': 'nfl', 'game_id': '9', 'occurred_utc': '2026-09-29T20:00:00+00:00',
              'title': 'Touchdown', 'file': 'live-test-1.mp4'}
    first = library.persist(record)
    sidecar = settings.clips_dir / 'live-test-1.json'
    enriched = json.loads(sidecar.read_text()) | {'social_score': .91, 'social_enrichment': {'threads': 2}}
    sidecar.write_text(json.dumps(enriched))
    second = library.persist(record | {'title': 'Touchdown (corrected)'}, new=False)
    assert second['social_score'] == .91 and second['captured_utc'] == first['captured_utc']
    assert library.catalog.get('live-test-1')['title'] == 'Touchdown (corrected)'
    assert library.catalog.patch('live-test-1', {'social_score': .95})['title'] == 'Touchdown (corrected)'
    assert library.catalog.patch('missing', {'x': 1}) is None
    assert row_count(temp_library) == before + 1


def test_systemic_segment_failure_surfaces_for_reconnect(tmp_path):
    lines = ['#EXTM3U', '#EXT-X-MEDIA-SEQUENCE:1', '#EXT-X-PROGRAM-DATE-TIME:2026-09-29T20:00:00Z']
    for n in range(1, 4):
        lines += ['#EXTINF:4.0,', f'/api/hls?seg{n}']
    client = FakeClient('\n'.join(lines), broken=['seg1', 'seg2', 'seg3'])
    archive = LiveSegmentBuffer(tmp_path / 'buf', 'http://127.0.0.1:3000')
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(archive.refresh(client, '/api/hls?playlist'))
    assert archive.health()['last_segment_error'] == 'HTTPStatusError'
