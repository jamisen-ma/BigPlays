import asyncio
import json
import time
from pathlib import Path

import pytest

from bigplays.config import settings
from bigplays.ingest.reddit_browser import BrowserReadUnavailable, BrowserRedditClient
from bigplays.media.timeline import atomic_json, iso
from bigplays.orchestrator.live_agent import GameMonitor, clip_id
from bigplays.orchestrator.social_ranker import ReactionJudgment, SocialMonitor, evidence_window


@pytest.fixture
def setup_gate(monkeypatch, tmp_path):
    for name, value in [('agent_dir', tmp_path / 'agent'), ('clips_dir', tmp_path / 'clips'),
                        ('social_clip_gate', 'legacy'), ('reddit_source', 'browser'),
                        ('reddit_enabled', True), ('use_llm', True), ('social_llm_provider', 'ollama')]:
        monkeypatch.setattr(settings, name, value)
    now = time.time()
    play = {'play_id': '1234', 'occurred': now - 120, 'period': 2, 'clock': '8:28', 'clock_seconds': 508,
            'text': 'Hughes 65-yard touchdown run', 'interesting': True, 'home_score': 7, 'away_score': 0}
    game = {'game_id': '123', 'league': 'ncaaf', 'name': 'Houston at Georgia Southern', 'starts_at': iso(now - 3600)}
    monitor = GameMonitor(game)
    monitor.plays = [play]
    monitor.aliases = [['Houston'], ['Georgia Southern']]
    monitor.observations = [{'time': now - 60, 'period': 2, 'clock': '8:28', 'clock_seconds': 508}]
    monitor.archive.segments = [{'start': now - 90, 'duration': 60, 'file': 'play.ts'},
                                {'start': now, 'duration': 30, 'file': 'ad.ts'}]
    return monitor, play


class Comments:
    def __init__(self, play):
        self.rows = [{'id': c, 'created': play['occurred'] + 60, 'author': c,
                      'body': 'OMGG that Hughes run was crazyyy', 'url': f'https://www.reddit.com/comments/abc/_/{c}/'}
                     for c in 'abc']

    async def find_thread(self, *args):
        return {'id': 'abc'}

    async def comments(self, *args):
        return self.rows


class Judge:
    def __init__(self, reaction='spectacular_play'):
        self.calls = 0
        self.reaction = reaction

    async def judge(self, *args):
        self.calls += 1
        if self.reaction == 'failure':
            raise TimeoutError()
        return ReactionJudgment(hype_score=.9, confidence=.9, reaction=self.reaction,
                                rationale='Fixture reaction assessment.', evidence_ids=['a', 'b'])


async def try_cut(monitor):
    calls = []

    async def cut(start, end, out, metadata, *, still_approved):
        assert still_approved()
        calls.append((start, end, metadata))
        atomic_json(out.with_suffix('.json'), metadata)

    monitor.archive.cut = cut
    task = asyncio.create_task(monitor.clips())
    await asyncio.sleep(.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return calls


def test_repeated_hype_from_distinct_people_counts_but_one_person_does_not():
    play = {'occurred': 1000}
    rows = [{'id': str(i), 'author': str(i), 'created': 1040 + i, 'body': 'OMGG crazyyy'} for i in range(5)]
    sample, metrics = evidence_window(rows, play, 1100, 990)
    assert len(sample) == metrics['hype_commenters'] == metrics['peak_15s_commenters'] == 5
    assert metrics['activity_ratio'] is None
    _, spam = evidence_window([c | {'author': 'same'} for c in rows], play, 1100, 990)
    assert spam['distinct_commenters'] == spam['hype_commenters'] == 1


def test_approval_precedes_cut_and_carries_evidence_into_historical_clip(setup_gate):
    monitor, play = setup_gate
    judge = Judge()
    monitor.social = SocialMonitor(monitor, Comments(play), judge)

    async def exercise():
        assert await try_cut(monitor) == []
        await monitor.social.tick()
        assert monitor.social.approved(play)
        assert list(settings.clips_dir.glob('*.json')) == []
        monitor.social = SocialMonitor(monitor, Comments(play), judge)  # Approval survives restart.
        calls = await try_cut(monitor)
        assert len(calls) == 1
        start, end, meta = calls[0]
        assert start == monitor.observations[0]['time'] - 12
        assert end == monitor.observations[0]['time'] + 4
        assert meta['social_assessment']['fan_backed']
        assert meta['reasons'] == ['initial_filter', 'fan_hype_approved']
        assert await try_cut(monitor) == []

    asyncio.run(exercise())
    assert judge.calls == 1


@pytest.mark.parametrize('reaction', ['routine', 'officiating', 'injury', 'unrelated', 'unclear', 'failure'])
def test_nonviral_or_failed_review_never_cuts(setup_gate, reaction):
    monitor, play = setup_gate
    monitor.social = SocialMonitor(monitor, Comments(play), Judge(reaction))

    async def exercise():
        await monitor.social.tick()
        assert not monitor.social.approved(play)
        assert await try_cut(monitor) == []

    asyncio.run(exercise())


def test_initial_filter_and_manual_override_are_explicit(setup_gate):
    monitor, play = setup_gate
    play['interesting'] = False
    judge = Judge()
    monitor.social = SocialMonitor(monitor, Comments(play), judge)

    async def exercise():
        await monitor.social.tick()
        assert judge.calls == 0
        assert await try_cut(monitor) == []
        atomic_json(monitor.request_path(play), {'play_id': play['play_id']})
        calls = await try_cut(monitor)
        assert len(calls) == 1 and calls[0][2]['reasons'] == ['manual_override']

    asyncio.run(exercise())


def test_deleted_evidence_and_corrected_play_revoke_pending_approval(setup_gate):
    monitor, play = setup_gate
    reddit = Comments(play)
    monitor.social = SocialMonitor(monitor, reddit, Judge())

    async def exercise():
        await monitor.social.tick()
        assert monitor.social.approved(play)
        assert not monitor.social.approved(play | {'text': 'Touchdown nullified by holding'})
        reddit.rows = [{'id': 'a', 'deleted': True}]
        await monitor.social.tick()
        assert not monitor.social.approved(play)
        assert await try_cut(monitor) == []

    asyncio.run(exercise())


def test_later_reactions_get_one_recheck_after_wait(setup_gate, monkeypatch):
    monitor, play = setup_gate
    clock = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    judge = Judge('routine')
    monitor.social = SocialMonitor(monitor, Comments(play), judge)

    async def exercise():
        await monitor.social.tick()
        assert not monitor.social.approved(play)
        judge.reaction = 'spectacular_play'
        await monitor.social.tick()
        assert judge.calls == 1
        clock[0] += 130
        await monitor.social.tick()
        assert judge.calls == 2 and monitor.social.approved(play)
        await monitor.social.tick()
        assert judge.calls == 2

    asyncio.run(exercise())


def test_play_correction_during_model_call_cannot_approve_old_play(setup_gate):
    monitor, play = setup_gate

    class CorrectingJudge(Judge):
        async def judge(self, *args):
            monitor.plays = [play | {'text': 'Touchdown nullified by holding'}]
            return await super().judge(*args)

    monitor.social = SocialMonitor(monitor, Comments(play), CorrectingJudge())

    async def exercise():
        await monitor.social.tick()
        assert not monitor.social.approved(monitor.plays[0])
        assert await try_cut(monitor) == []

    asyncio.run(exercise())


def test_missing_and_stale_reactions_cannot_approve(setup_gate):
    monitor, play = setup_gate
    reddit = Comments(play)
    for c in reddit.rows:
        c['created'] = play['occurred'] - 60
    judge = Judge()
    monitor.social = SocialMonitor(monitor, reddit, judge)

    async def exercise():
        await monitor.social.tick()
        assert judge.calls == 0
        assert monitor.social.decision_for(play)['status'] == 'insufficient_evidence'
        assert await try_cut(monitor) == []

    asyncio.run(exercise())


def test_browser_access_block_backs_off_across_games(monkeypatch):
    calls = []

    class Process:
        returncode = 0

        async def communicate(self):
            return json.dumps({'ok': False, 'status': 'browser_access_blocked'}).encode(), b''

    async def spawn(*args, **kwargs):
        calls.append(args)
        return Process()

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)

    async def exercise():
        client = BrowserRedditClient()
        try:
            for _ in range(2):
                with pytest.raises(BrowserReadUnavailable, match='browser_access_blocked'):
                    await client.read('threads', 'CFB')
        finally:
            await client.close()

    asyncio.run(exercise())
    assert len(calls) == 1


def test_cut_cannot_publish_if_approval_is_revoked_during_encoding(setup_gate, monkeypatch):
    monitor, play = setup_gate
    now = monitor.observations[0]['time']
    out = settings.clips_dir / 'revoked.mp4'

    class Encoder:
        returncode = 0

        async def communicate(self):
            return b'', b''

    async def spawn(*args, **kwargs):
        Path(args[-1]).write_bytes(b'fixture encoded output')
        return Encoder()

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(ValueError, match='approval changed'):
        asyncio.run(monitor.archive.cut(now - 12, now + 4, out, {}, still_approved=lambda: False))
    assert not out.exists() and not out.with_suffix('.json').exists()
    assert not out.with_suffix('.partial.mp4').exists()


def test_immediate_clip_is_enriched_later_without_changing_original_times(setup_gate, monkeypatch):
    monitor, play = setup_gate
    monkeypatch.setattr(settings, 'social_clip_gate', False)
    judge, reddit = Judge(), Comments(play)
    monitor.social = SocialMonitor(monitor, reddit, judge)
    path = settings.clips_dir / (clip_id(monitor.game, play['play_id']) + '.json')
    original = {'base_score': .6, 'combined_score': .6, 'occurred_utc': iso(play['occurred']),
                'received_utc': iso(play['occurred'] + 5), 'clip_start_utc': iso(play['occurred'] - 12),
                'file': '/clips/already-recorded.mp4'}
    atomic_json(path, original)

    async def exercise():
        await monitor.social.tick()
        saved = json.loads(path.read_text())
        assert saved['combined_score'] == .69
        assert saved['social_assessment']['fan_backed']
        for key in ('occurred_utc', 'received_utc', 'clip_start_utc', 'file'):
            assert saved[key] == original[key]
        monitor.social = SocialMonitor(monitor, reddit, judge)
        await monitor.social.tick()
        assert judge.calls == 1  # Persisted approval avoids a duplicate model call.
        reddit.rows = [{'id': 'a', 'deleted': True}]
        await monitor.social.tick()
        revoked = json.loads(path.read_text())
        assert revoked['social_assessment']['status'] == 'evidence_removed'
        assert revoked['combined_score'] == .6 and revoked['occurred_utc'] == original['occurred_utc']

    asyncio.run(exercise())


def test_immediate_clip_missing_credentials_does_not_modify_or_remove_clip(setup_gate, monkeypatch):
    monitor, play = setup_gate
    monkeypatch.setattr(settings, 'social_clip_gate', False)
    monkeypatch.setattr(settings, 'reddit_source', 'api')
    monkeypatch.setattr(settings, 'reddit_client_id', None)
    judge = Judge()
    monitor.social = SocialMonitor(monitor, Comments(play), judge)
    path = settings.clips_dir / (clip_id(monitor.game, play['play_id']) + '.json')
    original = {'occurred_utc': iso(play['occurred']), 'file': '/clips/already-recorded.mp4'}
    atomic_json(path, original)
    asyncio.run(monitor.social.tick())
    assert json.loads(path.read_text()) == original and judge.calls == 0
    assert monitor.social.status['status'] == 'needs_credentials'


def test_corrected_play_removes_previous_assessment_from_immediate_clip(setup_gate, monkeypatch):
    monitor, play = setup_gate
    monkeypatch.setattr(settings, 'social_clip_gate', False)
    monitor.social = SocialMonitor(monitor, Comments(play), Judge())
    path = settings.clips_dir / (clip_id(monitor.game, play['play_id']) + '.json')
    atomic_json(path, {'base_score': .6, 'occurred_utc': iso(play['occurred'])})

    async def exercise():
        await monitor.social.tick()
        assert json.loads(path.read_text())['social_assessment']['fan_backed']
        monitor.plays = [play | {'text': 'Touchdown nullified by holding', 'interesting': False}]
        await monitor.social.tick()
        corrected = json.loads(path.read_text())
        assert corrected['social_assessment']['status'] == 'play_corrected'
        assert corrected['combined_score'] == .6

    asyncio.run(exercise())
