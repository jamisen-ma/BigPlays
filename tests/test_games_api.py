import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bigplays.server import app as app_module
from bigplays.server import games
from bigplays.storage import play_links
from bigplays.storage.catalog import HighlightCatalog
from bigplays.storage.play_links import LinkIndex

ROOT = Path(__file__).resolve().parents[1]
REAL_DB = ROOT / 'data' / 'clips.sqlite3'
REAL_CLIPS = ROOT / 'data' / 'clips'

GAME = {'game_id': '401', 'league': 'nfl', 'status': 'post', 'start_utc': '2026-09-25T00:15:00Z',
        'away': {'abbr': 'GB'}, 'home': {'abbr': 'ATL'}}
PLAYS = [{'play_id': '40101', 'sequence': 1, 'text': 'kickoff'},
         {'play_id': '40102', 'sequence': 2, 'text': 'TD pass', 'scoring': True},
         {'play_id': '40103', 'sequence': 3, 'text': 'XP'}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Keep the app lifespan (sidecar migration, watchers) away from the real data/ directory.
    from bigplays.config import settings
    monkeypatch.setattr(settings, 'clips_dir', tmp_path / 'clips')
    monkeypatch.setattr(settings, 'database_path', tmp_path / 'lifespan.sqlite3')
    monkeypatch.setattr(settings, 'mlb_highlights_enabled', False)
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    db = tmp_path / 'clips.sqlite3'
    cat = HighlightCatalog(db)
    monkeypatch.setattr(play_links, 'index', LinkIndex(db, tmp_path))
    return cat


@pytest.fixture
def fake_gamecast(monkeypatch):
    mod = types.ModuleType('bigplays.ingest.gamecast')
    calls = []

    async def fetch_scoreboard(league, date=None):
        calls.append(('scoreboard', league, date))
        return [dict(GAME, league=league)]

    async def fetch_game(league, game_id):
        calls.append(('game', league, game_id))
        if game_id == 'missing':
            raise LookupError(game_id)
        if game_id == 'boom':
            raise RuntimeError('espn down')
        return {'game': dict(GAME, game_id=game_id), 'linescore': {'periods': [], 'totals': {}},
                'plays': [dict(p) for p in PLAYS]}

    mod.fetch_scoreboard, mod.fetch_game, mod.calls = fetch_scoreboard, fetch_game, calls
    monkeypatch.setitem(sys.modules, 'bigplays.ingest.gamecast', mod)
    return mod


def test_games_list_counts_clips(client, catalog, fake_gamecast):
    catalog.upsert({'event_id': 'a', 'game_id': '401', 'league': 'nfl', 'source_play_id': '40102'})
    catalog.upsert({'event_id': 'b', 'game_id': '401', 'league': 'nfl', 'play_candidates': ['40102']})
    catalog.upsert({'event_id': 'c', 'game_id': '401', 'league': 'nfl', 'play_id': '40103'})
    body = client.get('/api/games?league=nfl&date=20260924').json()
    assert body['ok'] and body['league'] == 'nfl' and body['date'] == '20260924'
    assert body['games'][0]['clip_count'] == 3 and body['games'][0]['viral_count'] == 2
    assert fake_gamecast.calls[-1] == ('scoreboard', 'nfl', '20260924')


def test_game_detail_attaches_and_reports_unmatched(client, catalog, fake_gamecast):
    catalog.upsert({'event_id': 'exact', 'game_id': '401', 'league': 'nfl', 'source_play_id': '40102',
                    'imported': True, 'social_score': 0.5})
    catalog.upsert({'event_id': 'live', 'game_id': '401', 'league': 'nfl', 'play_id': '40102',
                    'source_play_id': '40102', 'source_kind': 'live_capture'})
    catalog.upsert({'event_id': 'cand', 'game_id': '401', 'league': 'nfl', 'play_candidates': ['40103']})
    catalog.upsert({'event_id': 'lost', 'game_id': '401', 'league': 'nfl', 'source_play_id': 'zzz'})
    catalog.upsert({'event_id': 'other', 'game_id': '999', 'league': 'nfl', 'source_play_id': '40101'})
    res = client.get('/api/games/nfl/401')
    assert res.status_code == 200
    body = res.json()
    plays = body['plays']
    assert plays[0]['clip'] is None and plays[0]['viral'] is False
    assert plays[1]['clip']['event_id'] == 'live' and plays[1]['viral']
    assert [c['event_id'] for c in plays[1]['alternate_clips']] == ['exact']
    assert plays[2]['clip']['event_id'] == 'cand' and plays[2]['clip']['source_kind'] == 'live_capture'
    assert [c['event_id'] for c in body['clips_unmatched']] == ['lost']
    assert body['game']['clip_count'] == 4 and body['game']['viral_count'] == 2
    assert set(body) >= {'ok', 'game', 'linescore', 'updated_utc', 'plays', 'clips_unmatched'}


def test_new_clip_appears_without_restart(client, catalog, fake_gamecast):
    assert client.get('/api/games/nfl/401').json()['plays'][1]['clip'] is None
    catalog.upsert({'event_id': 'new', 'game_id': '401', 'league': 'nfl', 'play_id': '40102'})
    assert client.get('/api/games/nfl/401').json()['plays'][1]['clip']['event_id'] == 'new'


@pytest.mark.parametrize('path,status', [
    ('/api/games?league=nba', 400), ('/api/games/nba/401', 400), ('/api/games?league=nfl&date=2026-09-29', 400),
    ('/api/games?league=mlb&week=3', 400), ('/api/games/nfl/missing', 404), ('/api/games/nfl/boom', 502),
])
def test_errors_are_json(client, catalog, fake_gamecast, path, status):
    res = client.get(path)
    assert res.status_code == status
    body = res.json()
    assert body['ok'] is False and body['error']


def test_gamecast_missing_is_503(client, catalog, monkeypatch):
    monkeypatch.setitem(sys.modules, 'bigplays.ingest.gamecast', None)
    res = client.get('/api/games?league=mlb')
    assert res.status_code == 503 and res.json()['ok'] is False


def test_unknown_api_path_is_json_404(client):
    for path in ('/api/nope', '/api/games/nfl/401/extra/bits', '/api'):
        res = client.get(path)
        assert res.status_code == 404, path
        assert res.headers['content-type'].startswith('application/json')
        assert res.json() == {'ok': False, 'error': 'Not found'}


def test_spa_fallback_still_serves_html(client, tmp_path, monkeypatch):
    res = client.get('/some/client/route')
    assert res.status_code == 200 and 'text/html' in res.headers['content-type']
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<html>spa</html>')
    monkeypatch.setattr(app_module, 'FRONTEND_DIST', dist)
    for path in ('/', '/games/nfl/401', '/apiary'):
        res = client.get(path)
        assert res.status_code == 200 and 'spa' in res.text, path


# ---------------------------------------------------------------- real catalog data
def _real_catalog(tmp_path, monkeypatch):
    if not REAL_DB.exists():
        pytest.skip('no local catalog')
    copy = tmp_path / 'real.sqlite3'
    source = sqlite3.connect(f'file:{REAL_DB}?mode=ro', uri=True)
    target = sqlite3.connect(copy)
    source.backup(target)
    source.close()
    target.close()
    monkeypatch.setattr(play_links, 'index', LinkIndex(copy, REAL_CLIPS))


def _real_gamecast():
    try:
        import importlib
        return importlib.import_module('bigplays.ingest.gamecast')
    except ImportError:
        pytest.skip('bigplays.ingest.gamecast not available yet')


@pytest.mark.skipif(not os.environ.get('BIGPLAYS_NETWORK_TESTS'), reason='set BIGPLAYS_NETWORK_TESTS=1')
def test_real_week3_game_attaches_clips(client, tmp_path, monkeypatch):
    _real_gamecast()
    _real_catalog(tmp_path, monkeypatch)
    body = client.get('/api/games/nfl/401872948').json()
    attached = [p for p in body['plays'] if p['clip']]
    total = len(attached) + sum(len(p.get('alternate_clips') or []) for p in attached) + len(body['clips_unmatched'])
    assert total == body['game']['clip_count'] > 0
    assert any(p['clip']['event_id'] == 'nfl-c4dd037a-156a-4283-afe7-ea1a93516cd1'
               and p['play_id'] == '401872948682' for p in attached)
