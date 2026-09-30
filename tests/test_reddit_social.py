import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from bigplays.config import settings
from bigplays.ingest.reddit import RedditClient, configuration_status, matching_threads, parse_comments
from bigplays.media.timeline import atomic_json, iso
from bigplays.orchestrator.live_agent import GameMonitor, clip_id
from bigplays.orchestrator.social_ranker import ReactionJudgment, SocialMonitor, SocialJudge, apply_judgment, evidence_window
from bigplays.server.app import changed_highlights


@pytest.fixture(autouse=True)
def api_mode(monkeypatch):
    monkeypatch.setattr(settings, 'reddit_source', 'api')


def comment(cid, now, author=None, body=None):
    return {'id': cid, 'created': now, 'author': author or cid, 'body': body or f'Great run {cid}',
            'url': f'https://www.reddit.com/comments/abc/_/{cid}/'}


def judgment(**changes):
    return ReactionJudgment.model_validate(dict(hype_score=.9, confidence=.8, reaction='spectacular_play',
        rationale='Several distinct commenters praise the run.', evidence_ids=['a', 'b']) | changes)


def test_thread_match_requires_both_teams_game_thread_and_date():
    game = {'starts_at': iso(100000)}
    post = {'id': 'abc', 'title': '[Game Thread] Houston at Georgia Southern', 'created_utc': 99000}
    others = [post | {'title': '[Postgame Thread] Houston at Georgia Southern'},
              post | {'title': '[Game Thread] Houston at Alabama'}, post | {'created_utc': 1000}]
    assert matching_threads([post, *others], game, [['HOUSTON'], ['GEORGIA SOUTHERN']]) == [post]


def test_comments_remove_deleted_content_and_reject_other_threads():
    rows = [{'kind': 't1', 'data': {'id': cid, 'link_id': link, 'body': body, 'author': 'reader', 'created_utc': 100}}
            for cid, link, body in [('a', 't3_abc', 'What a run'), ('b', 't3_abc', '[deleted]'), ('c', 't3_other', 'wrong')]]
    parsed = parse_comments([{}, {'data': {'children': rows}}], 'abc')
    assert {c['id'] for c in parsed} == {'a', 'b'}
    assert next(c for c in parsed if c['id'] == 'b') == {'id': 'b', 'deleted': True}
    assert 'reader' not in json.dumps(parsed)


def test_spam_and_duplicate_authors_do_not_create_consensus():
    comments = [comment('a', 1010, 'one', 'Wow'), comment('b', 1011, 'one', 'Amazing'),
                comment('c', 1012, 'two', 'Wow'), comment('d', 1050, 'three', 'Great catch')]
    sample, metrics = evidence_window(comments, {'occurred': 1000}, 1100, 990)
    assert len(sample) == 3
    assert metrics['activity_ratio'] is None  # Do not invent a pre-start baseline.
    assert metrics['distinct_commenters'] == 3


def test_officiating_anger_and_invented_evidence_cannot_boost_a_clip():
    comments = [comment('a', 1), comment('b', 2), comment('c', 3)]
    meta = {'base_score': .6}
    ranked = apply_judgment(meta, judgment(reaction='officiating'), comments, {'distinct_commenters': 3}, 'abc', 1)
    assert ranked['combined_score'] == .6
    assert not ranked['social_assessment']['fan_backed']
    with pytest.raises(ValueError, match='not given'):
        apply_judgment({}, judgment(evidence_ids=['invented']), comments, {}, 'abc', 1)
    with pytest.raises(ValidationError):
        judgment(hype_score=4)
    with pytest.raises(ValidationError):
        judgment(confidence='certain')


def test_credentials_missing_makes_no_network_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'reddit_client_id', None)
    monitor = SimpleNamespace(directory=tmp_path)
    worker = SocialMonitor(monitor, None, None)
    asyncio.run(worker.tick())
    assert worker.status['status'] == 'needs_credentials'


def test_local_model_does_not_require_cloud_key(monkeypatch):
    for key, value in [('reddit_enabled', True), ('use_llm', True), ('reddit_client_id', 'test'),
                       ('reddit_client_secret', 'test'), ('anthropic_api_key', None),
                       ('social_llm_provider', 'ollama')]:
        monkeypatch.setattr(settings, key, value)
    assert configuration_status()['status'] == 'ready'
    assert configuration_status()['missing'] == []
    monkeypatch.setattr(settings, 'social_llm_provider', 'anthropic')
    assert configuration_status()['missing'] == ['ANTHROPIC_API_KEY']


@pytest.mark.parametrize('failure', [None, 'unavailable', 'truncated', 'invalid', 'invented'])
def test_local_structured_scoring_and_no_cloud_fallback(monkeypatch, tmp_path, failure):
    for key, value in [('social_llm_provider', 'ollama'), ('social_llm_model', 'qwen3:4b'),
                       ('ollama_base_url', 'http://127.0.0.1:11434'),
                       ('anthropic_api_key', None), ('agent_dir', tmp_path)]:
        monkeypatch.setattr(settings, key, value)
    requests = []
    sample = [comment(c, 100) for c in 'abc']
    def handle(request):
        requests.append(request)
        assert request.url.host == '127.0.0.1'
        assert 'x-api-key' not in request.headers
        body = json.loads(request.content)
        assert body['format']['additionalProperties'] is False
        assert body['think'] is False and body['stream'] is False
        context = json.loads(body['messages'][1]['content'])
        assert all('author' not in c for c in context['comments'])
        if failure == 'unavailable':
            raise httpx.ConnectError('Ollama offline', request=request)
        result = judgment(evidence_ids=['invented']) if failure == 'invented' else judgment()
        return httpx.Response(200, json={'done': True,
            'done_reason': 'length' if failure == 'truncated' else 'stop',
            'message': {'content': '{}' if failure == 'invalid' else result.model_dump_json()}})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await SocialJudge(client).judge({'name': 'Fixture game'}, {}, [], sample, {})
            return apply_judgment({'base_score': .6}, result, sample, {'distinct_commenters': 3}, 'abc', 1)
    if failure:
        with pytest.raises((ValueError, httpx.ConnectError)):
            asyncio.run(exercise())
    else:
        saved = asyncio.run(exercise())
        assert saved['social_assessment']['provider'] == 'ollama'
        assert saved['combined_score'] == .69
    assert len(requests) == 1


def test_oauth_and_rate_limit_backoff(monkeypatch):
    monkeypatch.setattr(settings, 'reddit_enabled', True)
    monkeypatch.setattr(settings, 'reddit_client_id', 'test-id')
    monkeypatch.setattr(settings, 'reddit_client_secret', 'test-secret')
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path.endswith('access_token'):
            return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 3600})
        assert request.headers['authorization'] == 'Bearer fixture-token'
        return httpx.Response(429, headers={'retry-after': '60'})
    async def exercise():
        client = RedditClient(httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        with pytest.raises(httpx.HTTPStatusError):
            await client.get('/comments/abc', {})
        with pytest.raises(ValueError, match='backoff'):
            await client.get('/comments/abc', {})
        await client.close()
    asyncio.run(exercise())
    assert len(requests) == 2


def test_mocked_reddit_to_llm_to_clip_metadata_and_restart_budget(monkeypatch, tmp_path):
    # Synthetic service responses validate the wiring, not live Reddit/API access.
    for key, value in [('reddit_enabled', True), ('use_llm', True), ('reddit_client_id', 'test-id'),
                       ('reddit_client_secret', 'test-secret'), ('anthropic_api_key', 'test-key'),
                       ('social_llm_provider', 'anthropic'),
                       ('agent_dir', tmp_path / 'agent'), ('clips_dir', tmp_path / 'clips')]:
        monkeypatch.setattr(settings, key, value)
    now = time.time()
    game = {'league': 'ncaaf', 'game_id': '123', 'name': 'Houston at Georgia Southern', 'starts_at': iso(now - 3600)}
    play = {'play_id': '1234', 'occurred': now - 120, 'text': 'Hughes runs for a touchdown', 'clock': '8:28', 'period': 2, 'interesting': True}
    monitor = SimpleNamespace(directory=tmp_path / 'game', game=game, aliases=[['HOUSTON'], ['GEORGIA SOUTHERN']], plays=[play])
    path = settings.clips_dir / (clip_id(game, '1234') + '.json')
    calls = []
    def handle(request):
        if request.url.host == 'api.anthropic.com':
            body = json.loads(request.content)
            context = json.loads(body['messages'][0]['content'])
            assert len(context['comments']) == 3
            assert all('author' not in c for c in context['comments'])
            calls.append(body)
            return httpx.Response(200, json={'stop_reason': 'tool_use', 'content': [
                {'type': 'tool_use', 'name': 'rank_play', 'input': judgment().model_dump()}]})
        return httpx.Response(500)
    class FixtureReddit:
        async def find_thread(self, *args): return {'id': 'abc'}
        async def comments(self, *args): return [comment(c, now - 60) for c in 'abc']
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            judge = SocialJudge(client)
            worker = SocialMonitor(monitor, FixtureReddit(), judge)
            await worker.tick()
            await worker.tick()  # No second call until the delayed update.
            restarted = SocialMonitor(monitor, FixtureReddit(), judge)
            await restarted.tick()
            assert restarted.approved(play)
            return restarted.decision_for(play)
    saved = asyncio.run(exercise())
    assert len(calls) == 1
    assert not path.exists()  # Social approval happens before any clip exists.
    assert saved['assessment']['fan_backed']
    assert saved['combined_score'] == .83
    assert 'Great run' not in (monitor.directory / 'social-reviews.json').read_text()


def test_metadata_updates_are_broadcast_without_creating_duplicate_highlights(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'clips_dir', tmp_path)
    path = tmp_path / 'one.json'
    seen = {}
    atomic_json(path, {'event_id': 'one'})
    assert changed_highlights(seen)[0][0] == 'highlight'
    atomic_json(path, {'event_id': 'one', 'combined_score': .9})
    assert changed_highlights(seen)[0][0] == 'highlight_update'
    assert changed_highlights(seen) == []


def test_espn_correction_clears_stale_social_endorsement(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'agent_dir', tmp_path / 'agent')
    monkeypatch.setattr(settings, 'clips_dir', tmp_path / 'clips')
    game = {'game_id': '123', 'league': 'ncaaf'}
    monitor = GameMonitor(game)
    monitor.plays = [{'play_id': '1234', 'text': 'Touchdown nullified by holding', 'clock': '1:17', 'period': 1,
                      'home_score': 7, 'away_score': 0}]
    path = settings.clips_dir / (clip_id(game, '1234') + '.json')
    atomic_json(path, {'description': 'Touchdown', 'base_score': .6, 'combined_score': .9,
                      'social_assessment': {'fan_backed': True}})
    monitor.correct_play_metadata()
    saved = json.loads(path.read_text())
    assert saved['title'] == 'Touchdown nullified by holding'
    assert saved['combined_score'] == .6
    assert saved['social_assessment']['status'] == 'play_corrected'
