import httpx
import pytest
from fastapi.testclient import TestClient
from bigplays.server.app import app
from bigplays.server import streams


def test_input_validation():
    with TestClient(app) as client:
        for url in ['https://evil.test/live/game', 'http://ppv.to/live/game', 'https://ppv.to/live/../a']:
            result = client.post('/api/stream', json={'url': url}).json()
            assert result['stage'] == 'input'
            assert not result['ok']


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
