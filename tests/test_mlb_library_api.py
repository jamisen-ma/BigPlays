from bigplays.server import app as server
from bigplays.storage.catalog import catalog_for


def test_mlb_and_week3_share_the_persistent_library(monkeypatch, tmp_path):
    clips = tmp_path / 'clips'
    clips.mkdir()
    monkeypatch.setattr(server.settings, 'clips_dir', clips)
    monkeypatch.setattr(server.settings, 'database_path', None)
    monkeypatch.setattr(server.settings, 'demo_mode', True)
    monkeypatch.setattr(server.settings, 'demo_dataset', 'nfl-2026-week3')
    catalog = catalog_for(clips)
    for event_id, league, dataset in (
        ('football', 'nfl', 'nfl-2026-week3'),
        ('baseball', 'mlb', 'mlb-2026-09-29'),
        ('older-football', 'nfl', 'highlights'),
    ):
        (clips / f'{event_id}.mp4').write_bytes(b'saved video')
        catalog.upsert({'event_id': event_id, 'league': league, 'replay_dataset': dataset,
                        'file': f'{event_id}.mp4', 'game_id': event_id})
    assert {r['event_id'] for r in server.load_highlights()} == {'football', 'baseball'}
    assert len(catalog.all()) == 3
    assert server.visible_highlight({'league': 'mlb'})


def test_mlb_schedule_restores_from_database_without_network(monkeypatch, tmp_path):
    monkeypatch.setattr(server.settings, 'clips_dir', tmp_path / 'clips')
    monkeypatch.setattr(server.settings, 'database_path', None)
    monkeypatch.setattr(server.settings, 'mlb_highlights_enabled', False)
    monkeypatch.setattr(server, '_sim', None)
    status = server.mlb_status()
    games = [{'game_id': 'mlb-game', 'league': 'mlb', 'inning': 8,
              'inning_half': 'bottom', 'clock': '', 'status': 'in'}]
    catalog_for(server.settings.clips_dir).save_import('mlb-' + status['date'],
        {'games': games, 'checked_at': '2026-09-29T23:00:00Z'})
    assert server.mlb_status()['checked_at'] == '2026-09-29T23:00:00Z'
    assert server.game_list() == games
