import httpx
import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from fastapi.testclient import TestClient
from bigplays.server.app import app
from bigplays.server import streams


def test_input_validation():
    with TestClient(app) as client:
        for url in ['https://evil.test/live/game', 'http://ppv.to/live/game', 'https://ppv.to/live/../a']:
            result = client.post('/api/stream', json={'url': url}).json()
            assert result['stage'] == 'input'
            assert not result['ok']


def test_current_provider_paths_are_accepted_without_allowing_traversal():
    from bigplays.ingest.ppv_provider import valid_event_url, valid_event_uri
    assert valid_event_url('https://ppv.st/live/ncaaf/2026-09-26/tex-ten')
    assert valid_event_url('https://ppv.to/live/nfl/2026-09-27/lac-buf')
    for uri in ['../admin', 'ncaaf/%2e%2e/admin', '//evil.test', 'ncaaf//game', 'game?q=a']:
        assert not valid_event_uri(uri)
        assert not valid_event_url('https://ppv.st/live/' + uri)


def test_relay_response_validation():
    assert streams.relay_path('http://127.0.0.1:3000/api/hls?sig=a') == '/api/hls?sig=a'
    for value in ['https://evil.test/api/hls?sig=a', '/other?sig=a', '//evil.test/api/hls?sig=a']:
        with pytest.raises(ValueError):
            streams.relay_path(value)


def test_gateway_resolve_and_hls(monkeypatch):
    monkeypatch.setattr(streams.settings, 'resolver_api_key', 'test-secret')
    calls = []
    def upstream(request):
        calls.append(request)
        if request.url.path == '/api/stream':
            assert request.headers['authorization'] == 'Bearer test-secret'
            return httpx.Response(200, json={'ok': True, 'proxiedUrl': 'http://127.0.0.1:3000/api/hls?sig=test'})
        return httpx.Response(200, text='#EXTM3U\n/api/hls?sig=segment', headers={'content-type': 'application/vnd.apple.mpegurl'})
    original = httpx.AsyncClient
    monkeypatch.setattr(streams.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(upstream), **kw))
    with TestClient(app) as client:
        result = client.post('/api/stream', json={'url': 'https://ppv.to/live/authorized-test'}).json()
        assert result['proxiedUrl'] == '/api/hls?sig=test'
        playlist = client.get(result['proxiedUrl'])
        assert playlist.status_code == 200
        assert 'localhost' not in playlist.text
    assert all(str(c.url).startswith('http://127.0.0.1:3000/') for c in calls)


def test_resolver_failure_stage_preserved(monkeypatch):
    monkeypatch.setattr(streams.settings, 'resolver_api_key', 'test-secret')
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={'ok': False, 'stage': 'decrypt', 'error': 'missing island header'}))
    monkeypatch.setattr(streams.httpx, 'AsyncClient', lambda **kw: original(transport=transport, **kw))
    with TestClient(app) as client:
        assert client.post('/api/stream', json={'url': 'https://ppv.to/live/test'}).json() == {'ok': False, 'stage': 'decrypt', 'error': 'missing island header'}


@pytest.mark.parametrize('game', [
    {'game_id': '401999123', 'league': 'nfl', 'name': 'Jets at Bills'},
    {'game_id': '401999456', 'league': 'nba', 'name': 'Celtics at Lakers'},
    {'game_id': '401999789', 'league': 'ncaaf', 'name': 'Texas at Tennessee'},
    None,
])
def test_recording_carries_game_to_clip_and_clears_on_stop(monkeypatch, tmp_path, game):
    monkeypatch.setattr(streams.settings, 'resolver_api_key', 'test-secret')
    monkeypatch.setattr(streams.settings, 'demo_mode', False)
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={
        'ok': True, 'proxiedUrl': '/api/hls?sig=test'}))
    monkeypatch.setattr(streams.httpx, 'AsyncClient', lambda **kw: original(transport=transport, **kw))
    class Buffer:
        segments_dir = tmp_path
        def __init__(self, config): self.proc = None
        def start(self): self.proc = SimpleNamespace(poll=lambda: None)
        def stop(self): self.proc = None
        def prune_old(self): pass
        def list_segments(self):
            now = datetime.now(timezone.utc)
            return [tmp_path / (t.strftime('%Y%m%d-%H%M%S') + '.ts')
                    for t in [now - timedelta(seconds=20), now]]
    monkeypatch.setattr(streams, 'StreamBuffer', Buffer)
    captured = []
    monkeypatch.setattr(streams, 'clip_recent', lambda *args: captured.append(args[-1]))
    with TestClient(app) as client:
        body = {'url': 'https://ppv.to/live/test', 'record': True, 'game': game}
        assert client.post('/api/stream', json=body).json()['recording']
        status = client.get('/api/recording').json()
        if game:
            assert status['game'] == game
        else:
            assert status['game']['league'] == 'unknown'
            assert status['game']['game_id'].startswith('manual-')
        assert client.post('/api/stream', json=body).status_code == 409
        assert not client.post('/api/recording/clip', json={}).json()['ok']
        monkeypatch.setattr(streams, 'recording_started', datetime.now(timezone.utc) - timedelta(minutes=1))
        assert client.post('/api/recording/clip', json={'seconds': 14}).json()['ok']
        assert captured[0]['league'] == status['game']['league']
        assert captured[0]['game_id'] == status['game']['game_id']
        assert client.post('/api/recording/stop').json()['recording'] is False
        assert client.get('/api/recording').json()['game'] is None
        assert streams.recording_started is None


def test_gateway_forwards_range(monkeypatch):
    def upstream(request):
        assert request.headers['range'] == 'bytes=4-7'
        return httpx.Response(206, content=b'abcd', headers={
            'content-type': 'video/mp4', 'content-range': 'bytes 4-7/20', 'accept-ranges': 'bytes'})
    original = httpx.AsyncClient
    monkeypatch.setattr(streams.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(upstream), **kw))
    with TestClient(app) as client:
        result = client.get('/api/hls?sig=test', headers={'Range': 'bytes=4-7'})
        assert result.status_code == 206
        assert result.content == b'abcd'
        assert result.headers['content-range'] == 'bytes 4-7/20'


def test_failed_start_does_not_leave_recording_state(monkeypatch):
    monkeypatch.setattr(streams.settings, 'resolver_api_key', 'test-secret')
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={'ok': True, 'proxiedUrl': '/api/hls?sig=test'}))
    monkeypatch.setattr(streams.httpx, 'AsyncClient', lambda **kw: original(transport=transport, **kw))
    def fail(self): raise RuntimeError('ffmpeg missing')
    monkeypatch.setattr(streams.StreamBuffer, 'start', fail)
    with TestClient(app) as client:
        response = client.post('/api/stream', json={'url': 'https://ppv.to/live/test', 'record': True})
        assert response.status_code == 500
        assert client.get('/api/recording').json() == {'ok': True, 'recording': False, 'game': None, 'error': None}


@pytest.mark.parametrize('payload', [[], None, {'ok': True, 'proxiedUrl': 42}])
def test_malformed_resolver_response(monkeypatch, payload):
    monkeypatch.setattr(streams.settings, 'resolver_api_key', 'test-secret')
    original = httpx.AsyncClient
    import json
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=json.dumps(payload)))
    monkeypatch.setattr(streams.httpx, 'AsyncClient', lambda **kw: original(transport=transport, **kw))
    with TestClient(app) as client:
        assert client.post('/api/stream', json={'url': 'https://ppv.to/live/test'}).status_code == 502
