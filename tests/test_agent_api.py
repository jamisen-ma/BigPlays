import time

from fastapi.testclient import TestClient

from bigplays.config import settings
from bigplays.media.timeline import atomic_json, iso
from bigplays.server.app import app


def test_agent_heartbeat_and_controls(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'agent_dir', tmp_path)
    client = TestClient(app)
    assert not client.get('/api/agent').json()['running']
    atomic_json(tmp_path / 'status.json', {'heartbeat': iso(time.time()), 'enabled': True})
    assert client.get('/api/agent').json()['running']
    assert client.post('/api/agent/control', json={'enabled': False}).json()['ok']
    assert (tmp_path / 'control.json').read_text() == '{"enabled": false}'
    atomic_json(tmp_path / 'status.json', {'heartbeat': iso(time.time() - 60)})
    assert not client.get('/api/agent').json()['running']


def test_agent_rejects_missing_history_and_queues_known_play(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'agent_dir', tmp_path)
    client = TestClient(app)
    status = {'heartbeat': iso(time.time()), 'enabled': True, 'games': [
        {'game': {'game_id': '123'}, 'plays': [
            {'play_id': '1231', 'status': 'outside recorded history'},
            {'play_id': '1232', 'status': 'waiting for matching on-screen clock'}]}]}
    atomic_json(tmp_path / 'status.json', status)
    assert client.post('/api/agent/clip', json={'game_id': '123', 'play_id': '1231'}).status_code == 422
    assert client.post('/api/agent/clip', json={'game_id': '999', 'play_id': '1232'}).status_code == 404
    assert client.post('/api/agent/clip', json={'game_id': '../123', 'play_id': '1232'}).status_code == 422
    assert client.post('/api/agent/clip', json={'game_id': '123', 'play_id': '1232'}).json()['ok']
    assert (tmp_path / 'requests/123-1232.json').exists()
