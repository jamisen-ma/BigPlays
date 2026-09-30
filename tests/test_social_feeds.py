import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from bigplays.ingest.social_feeds import (XRecentSearchClient, fetch_public_feed,
                                         iso, normalize_mastodon, normalize_x)


def mastodon(**changes):
    return {'id': '123', 'created_at': '2026-09-29T20:05:00Z', 'visibility': 'public',
            'url': 'https://mastodon.social/@reader/123', 'content': '<p>What a catch &amp; throw!</p>',
            'account': {'acct': 'reader', 'display_name': 'Reader', 'bot': False},
            'favourites_count': 4, 'replies_count': 2, 'reblogs_count': 1, **changes}


def test_public_feed_filters_private_bots_reposts_and_preserves_real_time():
    received = []
    def handle(request):
        received.append(request)
        assert request.url.path == '/api/v1/timelines/tag/MLB'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json=[mastodon(), mastodon(visibility='private'),
            mastodon(account={'acct': 'bot', 'bot': True}), mastodon(reblog=mastodon()),
            mastodon(sensitive=True), mastodon(created_at='invalid'), mastodon()])
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await fetch_public_feed('mlb', client=client)
    data = asyncio.run(exercise())
    assert data['status'] == 'ok' and len(data['posts']) == 1
    post = data['posts'][0]
    assert post['text'] == 'What a catch & throw!'
    assert post['created_at'] == '2026-09-29T20:05:00Z'
    assert post['collected_at'] == data['checked_at'] != post['created_at']
    assert post['metrics'] == {'likes': 4, 'replies': 2, 'reposts': 1}
    assert len(received) == 1


def test_public_feed_never_returns_unsafe_links_or_executes_html():
    assert normalize_mastodon(mastodon(url='javascript:alert(1)'), iso()) is None
    row = normalize_mastodon(mastodon(content='<p>Good catch</p><script>evil()</script>'), iso())
    assert row['text'] == 'Good catch'
    assert normalize_mastodon(mastodon(created_at='2026-09-29T20:05:00'), iso()) is None


@pytest.mark.parametrize('status,expected', [(401, 'needs_credentials'), (403, 'access_denied'),
    (429, 'rate_limited'), (500, 'error')])
def test_public_feed_reports_access_failure_without_alternate_endpoints(status, expected):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={'retry-after': '120'})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await fetch_public_feed('nfl', client=client)
    data = asyncio.run(exercise())
    assert data['status'] == expected and data['posts'] == []
    if status == 429:
        assert data['retry_after_seconds'] == 120
    assert len(requests) == 1


def test_x_cannot_make_paid_request_without_token_and_opt_in():
    def forbidden(request):
        raise AssertionError('No network request is permitted')
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
            for token, enabled, expected in [(None, True, 'needs_credentials'), ('secret', False, 'disabled')]:
                client = XRecentSearchClient(token, paid_enabled=enabled, client=http)
                assert (await client.search('NFL', '', ''))['status'] == expected
    asyncio.run(exercise())


def test_x_one_bounded_page_retains_replies_times_links_and_metrics():
    now = datetime.now(timezone.utc)
    start, end = iso(now - timedelta(hours=1)), iso(now - timedelta(minutes=1))
    created = iso(now - timedelta(minutes=30))
    rows = [{'id': '123', 'author_id': '456', 'text': 'That was a great catch', 'created_at': created,
             'conversation_id': '111', 'referenced_tweets': [{'type': 'replied_to', 'id': '111'}],
             'public_metrics': {'like_count': 5}},
            {'id': '124', 'author_id': '456', 'text': 'Bad time', 'created_at': iso(now)},
            {'id': '125', 'author_id': '456', 'text': 'No time'},
            {'id': '126', 'author_id': '456', 'text': 'Repost', 'created_at': created,
             'referenced_tweets': [{'type': 'retweeted', 'id': '123'}]}]
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.host == 'api.x.com'
        assert request.headers['authorization'] == 'Bearer fixture-secret'
        assert request.url.params['max_results'] == '10'
        assert request.url.params['start_time'] == start and request.url.params['end_time'] == end
        assert 'expansions' not in request.url.params  # No extra billed user lookups.
        return httpx.Response(200, json={'data': rows, 'meta': {'next_token': 'page2'}})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = XRecentSearchClient('fixture-secret', paid_enabled=True, client=http)
            return await client.search('NFL -is:retweet', start, end)
    data = asyncio.run(exercise())
    assert len(requests) == 1 and len(data['posts']) == 1 and data['next_cursor'] == 'page2'
    post = data['posts'][0]
    assert post['created_at'] == created and post['collected_at'] != created
    assert post['url'] == 'https://x.com/i/web/status/123' and post['metrics'] == {'likes': 5}
    assert 'fixture-secret' not in json.dumps(data)


def test_x_rate_limit_blocks_subsequent_requests():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(429, headers={'retry-after': '180'}, json={'detail': 'secret provider body'})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = XRecentSearchClient('fixture', paid_enabled=True, client=http)
            now = datetime.now(timezone.utc)
            for _ in range(2):
                result = await client.search('MLB', iso(now - timedelta(hours=2)), iso(now - timedelta(hours=1)))
                assert result['status'] == 'rate_limited' and result['retry_after_seconds'] >= 179
                assert 'secret provider body' not in json.dumps(result)
    asyncio.run(exercise())
    assert len(requests) == 1


def test_x_rejects_historical_window_before_any_paid_request():
    def forbidden(request):
        raise AssertionError('No request for an invalid date window')
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
            client = XRecentSearchClient('fixture', paid_enabled=True, client=http)
            now = datetime.now(timezone.utc)
            with pytest.raises(ValueError, match='seven days'):
                await client.search('NFL', iso(now - timedelta(days=8)), iso(now - timedelta(days=6)))
    asyncio.run(exercise())
