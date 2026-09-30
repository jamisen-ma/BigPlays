import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bigplays.config import settings
from bigplays.ingest import social_feeds
from bigplays.orchestrator.social_ranker import (SocialEnricher, apply_social_enrichment, classify_posts,
                                                 clip_terms, match_post)
from bigplays.server import social
from bigplays.storage.catalog import catalog_for

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ts(offset_seconds=0):
    return (NOW + timedelta(seconds=offset_seconds)).isoformat().replace('+00:00', 'Z')


def toot(pid, text, offset=0, acct=None, **changes):
    return {'id': str(pid), 'created_at': ts(offset), 'visibility': 'public',
            'url': f'https://mastodon.social/@{acct or "fan" + str(pid)}/{pid}', 'content': f'<p>{text}</p>',
            'account': {'acct': acct or f'fan{pid}', 'display_name': acct or f'Fan {pid}', 'bot': False},
            'favourites_count': 3, 'replies_count': 1, 'reblogs_count': 2, **changes}


CLIP = {'event_id': 'clip-watts', 'league': 'nfl', 'occurred_utc': ts(-600), 'received_utc': ts(-500),
        'clip_start_utc': ts(-610), 'clip_end_utc': ts(-590),
        'title': "Watts' late-game INT seals victory", 'description': 'Xavier Watts intercepts the pass.',
        'player': 'Xavier Watts', 'tags': ['Week 3', 'Xavier Watts'], 'away': 'ATL', 'home': 'GB',
        'reasons': ['turnover'], 'base_score': .8, 'combined_score': .8, 'file': 'clip-watts.mp4'}

ROWS = [
    toot(1, 'Xavier Watts with the pick! Falcons ball', -300),
    toot(2, 'Falcons get an interception, huge', -200),
    toot(3, 'Who else is watching football tonight?', -100),
    toot(1, 'Xavier Watts with the pick! Falcons ball', -300),       # duplicate id
    toot(4, 'Who else is watching football tonight?', -90, acct='fan3'),  # same author + text
    toot(5, 'Watts again? Different era', -4 * 86400),              # outside window
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    clips = tmp_path / 'clips'
    clips.mkdir()
    monkeypatch.setattr(settings, 'clips_dir', clips)
    monkeypatch.setattr(settings, 'database_path', tmp_path / 'catalog.sqlite3')
    monkeypatch.setattr(settings, 'reddit_client_id', None)
    monkeypatch.setattr(settings, 'reddit_client_secret', None)
    monkeypatch.setattr(social, '_env', lambda name: None)
    (clips / 'clip-watts.json').write_text(json.dumps(CLIP))
    (clips / 'clip-watts.mp4').write_bytes(b'video')
    catalog_for(clips, settings.database_path).upsert(CLIP)
    requests = []

    def handle(request):
        requests.append(request)
        assert 'authorization' not in request.headers
        return httpx.Response(200, json=ROWS)

    async def fetch(league):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await social_feeds.fetch_public_feed(league, client=client)

    clock = {'now': 1000.0}
    monkeypatch.setattr(social, 'feed_cache', social.FeedCache(fetch=fetch, clock=lambda: clock['now']))
    monkeypatch.setattr(social, 'enricher', SocialEnricher(social.cached_feed, clips))
    app = FastAPI()
    app.include_router(social.router)
    return TestClient(app), requests, clock, clips


def test_feed_is_deduplicated_attributed_classified_and_cached(env):
    client, requests, clock, _ = env
    data = client.get('/api/social/feed', params={'league': 'nfl', 'limit': 10}).json()
    assert data['ok'] and data['league'] == 'nfl' and data['cached'] is False
    ids = [p['id'] for p in data['posts']]
    assert ids == ['mastodon:4', 'mastodon:2', 'mastodon:1', 'mastodon:5']  # dup id + same author/text collapsed
    by_id = {p['id']: p for p in data['posts']}
    for post in data['posts']:
        assert post['url'].startswith('https://mastodon.social/') and post['created_at'] and post['author_key']
        assert post['relevance'] in ('general_chatter', 'play_evidence')
    watts = by_id['mastodon:1']
    assert watts['relevance'] == 'play_evidence'
    evidence = watts['play_matches'][0]
    assert evidence['clip_id'] == 'clip-watts' and evidence['players'] == ['Xavier Watts']
    assert evidence['teams'] == ['ATL'] and 'interception' in evidence['actions']
    assert evidence['confidence'] == 'high' and evidence['seconds_after_play'] == 300
    assert by_id['mastodon:2']['relevance'] == 'play_evidence'  # team + action in window
    assert by_id['mastodon:2']['play_matches'][0]['confidence'] == 'medium'
    assert by_id['mastodon:5']['relevance'] == 'general_chatter'  # name but days later
    assert data['providers']['mastodon']['status'] == 'ok' and data['providers']['mastodon']['fetched_at']
    assert data['providers']['x']['status'] == 'needs_token'
    assert data['providers']['reddit']['status'] == 'disabled'  # RSS poller off in tests (conftest)
    assert data['providers']['reddit']['judge']['status'] == 'missing_credentials'
    assert data['play_evidence_count'] == 2

    again = client.get('/api/social/feed', params={'league': 'nfl'}).json()
    assert again['cached'] is True and len(requests) == 1
    clock['now'] += 61
    client.get('/api/social/feed', params={'league': 'nfl'})
    assert len(requests) == 2
    client.get('/api/social/feed', params={'league': 'mlb'})
    assert len(requests) == 3 and requests[-1].url.path.endswith('/MLB')


def test_feed_rejects_bad_params(env):
    client = env[0]
    assert client.get('/api/social/feed', params={'league': 'nhl'}).status_code == 422
    assert client.get('/api/social/feed', params={'limit': 0}).status_code == 422


def test_provider_errors_keep_last_good_posts_and_report_status(env, monkeypatch):
    client, _, clock, _ = env
    client.get('/api/social/feed')
    calls = []

    async def failing(league):
        calls.append(league)
        return social_feeds.result('mastodon', 'rate_limited', error='Mastodon returned HTTP 429.', retry_after_seconds=300)

    social.feed_cache.fetch = failing
    clock['now'] += 61
    data = client.get('/api/social/feed').json()
    assert data['stale'] is True and data['count'] > 0
    assert data['providers']['mastodon']['status'] == 'error'
    assert data['providers']['mastodon']['detail'] == 'rate_limited'
    clock['now'] += 120  # retry-after (300s) outlasts the 60s TTL
    client.get('/api/social/feed')
    assert calls == ['nfl']


def test_status_is_connection_state_only(env, monkeypatch):
    client, requests, _, _ = env
    data = client.get('/api/social/status').json()
    assert requests == []
    assert data['providers']['mastodon']['status'] == 'not_checked'
    assert set(data['providers']['mastodon']['leagues']) == {'nfl', 'mlb'}
    monkeypatch.setattr(social, '_env', lambda name: {'X_BEARER_TOKEN': 'secret'}.get(name))
    data = client.get('/api/social/status').json()
    assert data['providers']['x']['status'] == 'disabled_paid' and 'secret' not in json.dumps(data)
    client.get('/api/social/feed')
    assert client.get('/api/social/status').json()['providers']['mastodon']['status'] == 'ok'


def test_play_endpoint_returns_only_evidence_and_enriches_after_publication(env):
    client, _, _, clips = env
    data = client.get('/api/social/play/clip-watts').json()
    assert data['ok'] and data['clip']['clip_id'] == 'clip-watts'
    assert {p['id'] for p in data['posts']} == {'mastodon:1', 'mastodon:2'}
    assert all(p['relevance'] == 'play_evidence' and p['play_matches'] for p in data['posts'])
    for _ in range(100):  # enrichment is a background task, not part of the response
        saved = json.loads((clips / 'clip-watts.json').read_text())
        if 'social_enrichment' in saved:
            break
        time.sleep(.02)
    for key in ('occurred_utc', 'received_utc', 'clip_start_utc', 'clip_end_utc', 'combined_score', 'base_score'):
        assert saved[key] == CLIP[key]
    assert saved['social_enrichment']['status'] == 'matched'
    assert saved['social_enrichment']['post_count'] == 2 and 0 < saved['social_score'] <= 1
    assert client.get('/api/social/play/missing').status_code == 404
    assert client.get('/api/social/play/..%2Fetc').status_code in (400, 404)


def test_enrichment_is_idempotent_and_accumulates_sources(tmp_path):
    path = tmp_path / 'c.json'
    path.write_text(json.dumps(CLIP))
    posts = classify_posts(social_feeds_posts(), [CLIP])
    evidence = [p for p in posts if p['relevance'] == 'play_evidence']
    providers = {'mastodon': {'status': 'ok'}}
    assert apply_social_enrichment(path, 'clip-watts', evidence[:1], providers)
    assert apply_social_enrichment(path, 'clip-watts', evidence[:1], providers) is None  # unchanged → no write
    assert apply_social_enrichment(path, 'clip-watts', evidence[1:], providers)['social_enrichment']['post_count'] == 2
    assert apply_social_enrichment(path, 'other-id', evidence, providers) is None


def test_enricher_skips_unpublished_clips_and_failed_providers(tmp_path):
    feeds = []

    async def feed(league):
        feeds.append(league)
        return {'posts': [], 'providers': {'mastodon': {'status': 'error'}}}

    enricher = SocialEnricher(feed, tmp_path)
    assert asyncio.run(enricher.enrich_once(CLIP)) is None and feeds == []  # no sidecar/video yet
    (tmp_path / 'clip-watts.json').write_text(json.dumps(CLIP))
    (tmp_path / 'clip-watts.mp4').write_bytes(b'v')
    assert asyncio.run(enricher.enrich_once(CLIP)) is None and feeds == ['nfl']
    assert 'social_enrichment' not in json.loads((tmp_path / 'clip-watts.json').read_text())

    async def schedule_returns_immediately():
        assert enricher.schedule(CLIP, checkpoints=(3600,)) is True
        assert enricher.schedule(CLIP) is False  # one task per clip
        enricher.tasks['clip-watts'].cancel()
    asyncio.run(schedule_returns_immediately())


def test_lexical_matching_rules():
    terms = clip_terms({**CLIP, 'name': 'Atlanta Falcons at Green Bay Packers'})
    assert 'Xavier Watts' in terms['players'] and 'interception' in terms['actions']
    post = lambda text, offset=-300: {'text': text, 'created_at': ts(offset)}
    assert match_post(post('great game by the Packers'), CLIP) is None  # team without action
    assert match_post(post('NO way that touchdown happened'), {**CLIP, 'home': 'NO'}) is None
    assert match_post(post('Watts!!'), CLIP)['players'] == ['Xavier Watts']
    assert match_post(post('watts of power'), CLIP) is None  # surname must be capitalized
    assert match_post(post('Xavier Watts', offset=-900), CLIP) is None  # before the play
    live = {'event_id': 'x', 'league': 'nfl', 'occurred_utc': ts(-60), 'title': 'J.Hurts 12 yard pass to A.Brown for a TD'}
    assert match_post(post('Hurts to Brown touchdown!', -30), live)['players'] == ['Brown', 'Hurts']


def social_feeds_posts():
    collected = social_feeds.iso()
    return [p for row in ROWS[:2] if (p := social_feeds.normalize_mastodon(row, collected))]


def test_app_includes_social_router():
    from bigplays.server.app import app
    data = TestClient(app).get('/api/social/status').json()  # no lifespan: nothing is fetched
    assert set(data['providers']) == {'mastodon', 'x', 'reddit'}
