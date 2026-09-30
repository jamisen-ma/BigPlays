"""Reddit fan-hype gate: heuristics, burst, can't-miss rules, LLM parsing, and cut -> hold -> publish.

Realistic comments come from tests/fixtures/reddit/padres_hype_thread.json: tonight's r/Padres
game thread (CHC@SD) from the RSS store with usernames, author hashes and comment ids replaced.
"""
import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx
import pytest

from bigplays.config import clip_gate_mode, settings
from bigplays.ingest import reddit_rss
from bigplays.ingest.reddit_rss import RateLimiter, RedditRSSPoller
from bigplays.media.timeline import atomic_json, iso, timestamp
from bigplays.orchestrator import hype_gate as hg
from bigplays.orchestrator.live_agent import GameMonitor, clip_id
from bigplays.storage.catalog import catalog_for
from bigplays.storage.play_links import LinkIndex

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'reddit' / 'padres_hype_thread.json').read_text())
THREAD = FIXTURE['thread']
GAME_ID = '401907974'
HYPE_TEXTS = ['OMGGGGG', 'LETS GOOOOO!!!', 'WHAT A CATCH', 'HOLY SHIT MERRILL', 'NO WAY HE CAUGHT THAT',
              'LFGSD \U0001f525\U0001f525', 'ARE YOU KIDDING ME', 'INSANE', 'SPIDERMAN!!!', 'OH MY GOD']
CALM_TEXTS = ['nice', 'ok then', 'Hoerner is annoying to face', 'anyone know if Arraez plays tomorrow']


# --------------------------------------------------------------------------- helpers

def real_record():
    return {'game': FIXTURE['game'], 'threads': {'padres:game_thread': dict(THREAD)},
            'posts': [dict(p) for p in FIXTURE['posts']]}


def real_play(name):
    play = dict(FIXTURE['plays'][name])
    return play, timestamp(play.pop('anchor_utc'))


def post(i, t, text, poll_at):
    return {'id': f'p{i}', 'text': text, 'created_at': iso(t), 'collected_at': iso(poll_at),
            'author_key': f'fan{i}', 'thread_url': THREAD['url']}


def thread_posts(anchor, window_texts, *, baseline_per_min=5, poll_at=None):
    """A thread with steady chatter for the previous 10 minutes, then ``window_texts`` after the play."""
    poll_at = anchor + 40 if poll_at is None else poll_at
    posts = [post(i, anchor - 605 + i * 60 / baseline_per_min, 'regular chatter about the game', poll_at)
             for i in range(10 * baseline_per_min)]
    posts += [post(1000 + i, anchor - 3 + i * 1.5, text, poll_at) for i, text in enumerate(window_texts)]
    return posts


def write_store(posts=None, polls=None, *, threads=True, game=True):
    data = {'version': 1, 'games': {}}
    if game:
        thread = dict(THREAD, polls=polls or [])
        data['games'][GAME_ID] = {'game': FIXTURE['game'], 'status': 'in',
                                  'threads': {'padres:game_thread': thread} if threads else {},
                                  'posts': posts or []}
    path = Path(settings.reddit_rss_state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    stamp = time.time() + len(json.dumps(data)) % 97  # a distinct mtime per write for the store cache
    os.utime(path, (stamp, stamp))


class FakeJudge:
    def __init__(self, viral=True, hype=.9, error=None, hang=False):
        self.viral, self.hype, self.error, self.hang = viral, hype, error, hang
        self.calls = []

    async def evaluate(self, game, play, evidence, previous=None):
        self.calls.append({'play': play['text'], 'sample': list(evidence['sample'])})
        if self.hang:
            await asyncio.sleep(3600)
        if self.error:
            raise self.error
        return {'viral': self.viral, 'hype': self.hype, 'reason': 'fans erupt about the catch',
                'quotes': [evidence['sample'][0], 'an invented quote'], 'latency_ms': 900}


@pytest.fixture
def hype_env(monkeypatch, tmp_path):
    for name, value in [('agent_dir', tmp_path / 'agent'), ('clips_dir', tmp_path / 'clips'),
                        ('database_path', tmp_path / 'clips.sqlite3'), ('social_clip_gate', 'hype'),
                        ('reddit_rss_enabled', True), ('reddit_rss_state_path', tmp_path / 'social' / 'reddit_rss.json'),
                        ('social_llm_provider', 'ollama'), ('use_llm', True), ('social_hype_window_seconds', 30),
                        ('social_hype_pre_seconds', 5), ('social_hype_fallback_seconds', 90),
                        ('social_hype_threshold', .45), ('social_hype_strict_threshold', .7),
                        ('social_hype_llm_timeout_seconds', 20.0), ('social_llm_calls_per_hour', 200)]:
        monkeypatch.setattr(settings, name, value)
    return tmp_path


def make_monitor(play, anchor, *, league='mlb', judge=None):
    game = {'game_id': GAME_ID, 'league': league, 'name': 'Chicago Cubs at San Diego Padres'}
    monitor = GameMonitor(game, hype_judge=judge or FakeJudge())
    monitor.plays = [play]
    monitor.locate = lambda p: {'start': anchor - 12, 'end': anchor + 4, 'anchor': anchor, 'match': {}}
    monitor.archive.segments = [{'start': anchor - 60, 'duration': 120, 'file': 'x.ts'}]

    async def cut(start, end, out, metadata, *, still_approved):
        assert still_approved()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b'mp4')
        out.with_suffix('.jpg').write_bytes(b'jpg')
        return {'mode': 'copy', 'cut_seconds': .1, 'poster': out.with_suffix('.jpg').name}
    monitor.archive.cut = cut
    return monitor


def mlb_play(occurred, text='Suzuki lined out to center.', **extra):
    return {'play_id': '4019079741606990057', 'occurred': occurred, 'period': 8, 'inning': 8, 'inning_half': 'Top',
            'period_label': 'Top 8', 'clock': '', 'text': text, 'interesting': True, 'home_score': 3,
            'away_score': 2, 'home_score_before': 3, 'away_score_before': 2, 'batter': 'Seiya Suzuki',
            'pitcher': 'Robert Suarez', 'event_type': 'line-out', **extra}


def catalog():
    return catalog_for(settings.clips_dir, settings.database_path)


def published_ids():
    from bigplays.server.app import load_highlights
    return [h['event_id'] for h in load_highlights()]


# --------------------------------------------------------------------------- settings

def test_gate_modes_keep_false_supported_and_true_means_hype():
    assert [clip_gate_mode(v) for v in (False, 'false', '0', 'off', True, 'true', 'hype', 'legacy')] == \
        ['off', 'off', 'off', 'off', 'hype', 'hype', 'hype', 'legacy']
    with pytest.raises(ValueError):
        clip_gate_mode('sometimes')


# --------------------------------------------------------------------------- per-comment heuristics

@pytest.mark.parametrize('text,low,high', [
    ('OMGGGG', .5, 1), ('OMG OMG OMG', .5, 1), ('LETSSSGOOOO!!!', .5, 1), ('NOOOOOOO', .5, 1),
    ('WHAT A CATCH \U0001f525\U0001f525', .5, 1), ('LFGSD', .5, 1), ('HOW', .3, .5), ('Curve looking nasty', .25, .45),
    ('Move back', 0, 0), ('anyone know if Arraez plays tomorrow', 0, .05), ('https://giphy.com/gifs/abc123', 0, 0),
])
def test_comment_hype_reads_caps_elongation_punctuation_lexicon_and_emoji(text, low, high):
    assert low <= hg.comment_hype(text)['score'] <= high


def test_comment_hype_credits_named_players_and_damps_long_analysis():
    names = hg.mention_pattern(['Joe', 'Musgrove'])
    assert hg.comment_hype('WOW JOE!!', names)['score'] > hg.comment_hype('WOW!!', names)['score']
    assert hg.comment_hype('Joe Musgrove is pitching', names)['score'] == 0  # a name alone is not hype
    essay = 'Honestly the insane thing is ' + 'how well the rotation has held up over a long season ' * 4
    assert hg.comment_hype(essay)['score'] < hg.comment_hype('insane')['score']


def test_pre_score_needs_several_distinct_hype_commenters():
    assert hg.pre_score([.9], None)['score'] < .45  # one excited person is not a crowd
    assert hg.pre_score([.9] * 8 + [.1] * 4, 3.0)['score'] >= .8
    assert hg.pre_score([.1] * 20, 1.0)['score'] < .2


# --------------------------------------------------------------------------- realistic comment sets

def test_real_thread_final_out_is_hype_and_routine_single_after_a_big_moment_is_not():
    conforto, anchor = real_play('conforto')  # final out, LFGSD / OMG OMG OMG burst
    final_out = hg.assess(real_record(), conforto, anchor, pre=5, post=30)
    assert final_out['data'] == 'ok' and final_out['pre_score'] >= .7
    assert final_out['burst_ratio'] > 2 and final_out['hype_commenters'] >= 6
    assert len(final_out['sample']) <= hg.LLM_SAMPLE and final_out['quotes']

    suzuki, anchor = real_play('suzuki')  # single 66 s after Musgrove's first strikeout: leftover buzz
    single = hg.assess(real_record(), suzuki, anchor, pre=5, post=30)
    assert single['data'] == 'ok' and single['pre_score'] < .45 and single['burst_ratio'] < 1.2

    hoerner, anchor = real_play('hoerner')  # the poller missed 04:46-04:51 (newest-100 feed)
    assert hg.assess(real_record(), hoerner, anchor, pre=5, post=30)['data'] == 'not_covered'


def test_evidence_never_carries_author_identities():
    conforto, anchor = real_play('conforto')
    evidence = hg.assess(real_record(), conforto, anchor, pre=5, post=30)
    authors = {p['author_key'] for p in FIXTURE['posts']}
    strings = set(re.findall(r'"([^"]*)"', json.dumps(evidence, ensure_ascii=False)))
    assert not strings & authors
    assert 'author' not in json.dumps(evidence)


def test_coverage_is_inferred_from_batches_and_read_from_the_poll_log():
    spans = hg.thread_coverage(THREAD, [p | {'_t': timestamp(p['created_at'])} for p in FIXTURE['posts']])
    gap = timestamp('2026-09-30T04:48:00Z')
    assert len(spans) == 2 and not hg.is_covered(spans, gap)
    assert hg.is_covered(spans, timestamp('2026-09-30T04:42:30Z'))
    log = {'url': 'u', 'polls': [{'at': 1000, 'outcome': 'ok', 'entries': 100, 'oldest': 900},
                                 {'at': 1060, 'outcome': 'rate_limited'},
                                 {'at': 1120, 'outcome': 'ok', 'entries': 40, 'oldest': 1050},
                                 {'at': 1300, 'outcome': 'ok', 'entries': 100, 'oldest': 1250}]}
    assert hg.thread_coverage(log, []) == [(900.0, 1120.0), (1250.0, 1300.0)]


# --------------------------------------------------------------------------- burst detection

def test_burst_ratio_spikes_against_the_median_baseline_minute():
    spans = [(0, 1000)]
    baseline = [i * 10.0 for i in range(60)]  # 6 comments/min for ten minutes
    window = [600 + i for i in range(30)]     # 60 comments/min right after the play
    spike = hg.burst_ratio(baseline + window, spans, 600, 630)
    assert spike['ratio'] >= 8 and spike['baseline_per_min'] == 6.0
    earlier = [300 + i * .5 for i in range(60)]  # the previous big play's burst
    assert hg.burst_ratio(baseline + earlier + window, spans, 600, 630)['ratio'] >= 8
    steady = [i * 10.0 for i in range(64)]
    assert hg.burst_ratio(steady, spans, 600, 629)['ratio'] < 1.5
    unknown = hg.burst_ratio(window, [(590, 700)], 600, 630)  # baseline minutes were never polled
    assert unknown['ratio'] is None and unknown['window_per_min'] == 60.0


# --------------------------------------------------------------------------- can't-miss plays

@pytest.mark.parametrize('league,play,expected', [
    ('mlb', {'text': 'Machado homered to left (402 feet).', 'event_type': 'home-run', 'inning': 3,
             'inning_half': 'Bottom', 'home_score': 1, 'away_score': 0, 'home_score_before': 0, 'away_score_before': 0},
     'home_run'),
    ('mlb', {'text': 'Tatis Jr. homered to left, Arraez, Machado and Merrill scored.', 'event_type': 'home-run',
             'inning': 2, 'inning_half': 'Bottom', 'home_score': 4, 'away_score': 1, 'home_score_before': 0,
             'away_score_before': 1}, 'grand_slam'),
    ('mlb', {'text': 'Merrill singled to right, Tatis Jr. scored.', 'event_type': 'single', 'inning': 9,
             'inning_half': 'Bottom', 'home_score': 3, 'away_score': 2, 'home_score_before': 2, 'away_score_before': 2},
     'walk_off'),
    ('mlb', {'text': 'Suzuki doubled to left, Happ scored.', 'inning': 8, 'inning_half': 'Top', 'home_score': 2,
             'away_score': 3, 'home_score_before': 2, 'away_score_before': 2}, 'go_ahead_late'),
    ('mlb', {'text': 'Suzuki singled to center, Happ scored.', 'inning': 7, 'inning_half': 'Top', 'home_score': 3,
             'away_score': 3, 'home_score_before': 3, 'away_score_before': 2}, 'tying_late'),
    ('mlb', {'text': 'Suzuki doubled to left, Happ scored.', 'inning': 6, 'inning_half': 'Top', 'home_score': 2,
             'away_score': 3, 'home_score_before': 2, 'away_score_before': 2}, None),  # go-ahead, but the 6th
    ('mlb', {'text': 'Conforto struck out looking.', 'event_type': 'strikeout', 'inning': 9, 'inning_half': 'Top',
             'home_score': 8, 'away_score': 0, 'home_score_before': 8, 'away_score_before': 0}, None),
    ('mlb', {'text': 'Hoerner reached on infield single to third.', 'inning': 7, 'inning_half': 'Top',
             'home_score': 7, 'away_score': 0}, None),
    ('nfl', {'text': 'J.Allen pass short right to D.Knox for 6 yards, TOUCHDOWN.'}, 'touchdown'),
    ('nfl', {'text': 'P.Mahomes pass deep left intended for T.Kelce INTERCEPTED by K.Hamilton at BAL 10.'},
     'interception'),
    ('nfl', {'text': 'J.Hurts sacked at PHI 20 for -8 yards. FUMBLES, RECOVERED by DAL-M.Parsons at PHI 20.'},
     'fumble_lost'),
    ('nfl', {'text': 'J.Allen pass to S.Diggs for 20 yards, TOUCHDOWN. PENALTY on BUF, Offensive Holding, '
                     '10 yards, enforced at KC 20 - No Play.'}, None),
    ('nfl', {'text': 'J.Cook right tackle for 22 yards.'}, None),
    ('nba', {'text': 'LeBron James makes 30-foot three point jumper'}, None),
])
def test_cant_miss_rules(league, play, expected):
    assert hg.cant_miss_reason(play, league) == expected


def test_cant_miss_uses_the_previous_play_when_pre_play_scores_are_missing():
    play = {'text': 'Merrill singled to right, Tatis Jr. scored.', 'inning': 10, 'inning_half': 'Bottom',
            'home_score': 5, 'away_score': 4}
    assert hg.cant_miss_reason(play, 'mlb', previous={'home_score': 4, 'away_score': 4}) == 'walk_off'
    assert hg.cant_miss_reason(play, 'mlb') is None


# --------------------------------------------------------------------------- LLM verdict parsing

def test_llm_json_parsing_strips_think_blocks_fences_and_usernames():
    reply = ('<think>\nfans are loud\n</think>\n```json\n{"viral": true, "hype": 0.91, '
             '"reason": "Walk-off, u/someone agrees", "quotes": ["OMGGGG", "LETS GO", "x", "y"]}\n```')
    assert hg.parse_llm_verdict(reply) == {'viral': True, 'hype': .91, 'reason': 'Walk-off, u/[user] agrees',
                                           'quotes': ['OMGGGG', 'LETS GO', 'x']}
    assert hg.parse_llm_verdict('{"viral": "false", "hype": 0, "reason": "routine"}')['viral'] is False
    assert hg.grounded_quotes(['LETS GO', 'made up'], ['LETS GO padres'], ['fallback']) == ['LETS GO']
    assert hg.grounded_quotes(['made up'], ['LETS GO padres'], ['fallback']) == ['fallback']


@pytest.mark.parametrize('reply', [None, '', 'not json', '<think>still thinking {"viral": true',
                                   '{"viral": "maybe", "hype": 0.5, "reason": "x"}',
                                   '{"viral": true, "hype": 7, "reason": "x"}',
                                   '{"viral": true, "hype": "high", "reason": "x"}',
                                   '{"viral": true, "hype": 0.5, "quotes": "OMG"}', '[1, 2]', '{"hype": 0.5}'])
def test_llm_json_parsing_rejects_malformed_output(reply):
    with pytest.raises(ValueError):
        hg.parse_llm_verdict(reply)


def test_ollama_request_disables_thinking_and_parses_the_reply(hype_env):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={'done': True, 'done_reason': 'stop', 'message': {
            'content': '<think></think>{"viral": true, "hype": 0.8, "reason": "Big catch", "quotes": ["OMG"]}'}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await hg.HypeJudge(client).evaluate(
                {'name': 'Chicago Cubs at San Diego Padres', 'league': 'mlb'}, mlb_play(1000),
                {'window': [0, 30], 'sample': ['OMG']})
    result = asyncio.run(run())
    assert result['viral'] is True and result['hype'] == .8 and 'latency_ms' in result
    assert seen['think'] is False and seen['model'] == settings.social_llm_model
    assert seen['messages'][-1]['content'].endswith('/no_think')
    assert 'Chicago Cubs 2, San Diego Padres 3' in seen['messages'][-1]['content']


# --------------------------------------------------------------------------- decision rules

def test_verdict_needs_pre_score_and_llm_and_falls_back_to_a_stricter_bar():
    rules = {'threshold': .45, 'strict': .7}
    strong = {'pre_score': .8, 'quotes': ['OMGGGG']}
    assert hg.verdict(strong, {'status': 'ok', 'viral': True, 'hype': .9}, **rules)[:2] == ('approved', .85)
    assert hg.verdict(strong, {'status': 'ok', 'viral': False, 'hype': .2, 'reason': 'umpire anger'},
                      **rules)[0] == 'rejected'
    assert hg.verdict(strong, {'status': 'unavailable'}, **rules)[0] == 'approved'
    assert hg.verdict({'pre_score': .6}, {'status': 'unavailable'}, **rules)[0] == 'rejected'
    assert hg.verdict({'pre_score': .18, 'quotes': ['nice']}, None, **rules) == \
        ('rejected', .18, 'rejected: low fan hype (0.18) — "nice"')


@pytest.mark.parametrize('data', ['no_game', 'no_thread', 'waiting_for_poll', 'not_covered', 'quiet_thread', 'ok'])
def test_gate_never_waits_past_its_deadline(data):
    evidence = {'data': data, 'distinct_commenters': 1}
    for now in (100, 101, 10_000):
        assert hg.evidence_state(evidence, now=now, deadline=100, repolled=False) in ('judge', 'fallback')


# --------------------------------------------------------------------------- cut -> hold -> publish

def test_held_clip_stays_hidden_then_publishes_the_moment_fans_approve(hype_env):
    anchor = time.time() - 60
    play = mlb_play(anchor - 20)
    judge = FakeJudge()
    monitor = make_monitor(play, anchor, judge=judge)
    event_id = clip_id(monitor.game, play['play_id'])
    held = hg.pending_paths(event_id)
    links = LinkIndex(settings.database_path, settings.clips_dir)
    from bigplays.server.app import changed_highlights
    seen = {}

    asyncio.run(monitor.clip_play(play))
    assert held['mp4'].exists() and held['jpg'].exists() and held['json'].exists()
    assert not list(settings.clips_dir.glob(event_id + '.*'))  # not under /clips either
    assert catalog().get(event_id) is None and event_id not in published_ids()
    assert changed_highlights(seen) == [] and links.refresh() == []  # no SSE highlight / game_update
    assert monitor.play_status(play).startswith('held: waiting for Reddit reactions')
    request = reddit_rss.read_priority(reddit_rss.priority_path())[f'{GAME_ID}:{play["play_id"]}']
    assert request['not_before'] == pytest.approx(anchor + 30 + hg.INGEST_SECONDS)

    write_store(thread_posts(anchor, HYPE_TEXTS), [{'at': anchor + 40, 'outcome': 'ok', 'entries': 100,
                                                     'oldest': anchor - 700}])
    asyncio.run(monitor.social.tick())
    assert len(judge.calls) == 1 and len(judge.calls[0]['sample']) <= hg.LLM_SAMPLE
    record = catalog().get(event_id)
    assert record['reasons'] == ['initial_filter', 'fan_hype_approved']
    hype = record['fan_hype']
    assert hype['status'] == 'approved' and hype['decision'].startswith('approved: fan hype')
    assert hype['llm']['viral'] is True and hype['pre_score'] >= .45 and 1 <= len(hype['quotes']) <= 3
    assert 'an invented quote' not in hype['quotes']
    assert not re.search(r'\bfan\d+\b|author', json.dumps(record))  # never usernames or author hashes
    for key in ('cut_at', 'decision_at', 'published_at', 'event_to_publish_seconds', 'decision_latency_seconds'):
        assert key in record['capture']
    assert (settings.clips_dir / f'{event_id}.mp4').exists() and not held['mp4'].exists() and not held['json'].exists()
    assert event_id in published_ids() and monitor.play_status(play) == 'clipped'
    assert [event for event, _ in changed_highlights(seen)] == ['highlight']
    assert links.refresh() == [('mlb', GAME_ID)]
    assert reddit_rss.read_priority(reddit_rss.priority_path()) == {}
    assert monitor.publish_pending(play['play_id'], reasons=['initial_filter']) is None  # idempotent


@pytest.mark.parametrize('store', ['missing', 'no_thread', 'rate_limited', 'quiet'])
def test_dark_reddit_falls_back_to_the_initial_filter_after_the_timeout(hype_env, store):
    now = [time.time()]
    anchor = now[0] - 10
    play = mlb_play(anchor - 20)
    monitor = make_monitor(play, anchor)
    monitor.social.clock = lambda: now[0]
    if store == 'no_thread':
        write_store(threads=False)
    elif store == 'rate_limited':
        write_store([], [{'at': anchor + 40, 'outcome': 'rate_limited'}])
    elif store == 'quiet':  # a dead thread: polled fine, but nobody has posted for ten minutes
        write_store([], [{'at': anchor + 60, 'outcome': 'ok', 'entries': 0, 'oldest': None}])
    asyncio.run(monitor.clip_play(play))
    event_id = clip_id(monitor.game, play['play_id'])
    deadline = anchor + 30 + hg.INGEST_SECONDS + settings.social_hype_fallback_seconds
    for t in (anchor + 36, deadline - 1):
        now[0] = t
        asyncio.run(monitor.social.tick())
        assert catalog().get(event_id) is None and monitor.play_status(play).startswith('held:')
    now[0] = deadline + 1
    asyncio.run(monitor.social.tick())
    record = catalog().get(event_id)
    assert record['reasons'] == ['initial_filter', 'fan_hype_unavailable']
    assert record['fan_hype']['status'] == 'fallback'
    assert record['fan_hype']['decision'].startswith('fallback: no Reddit data')


def test_low_hype_rejects_keeps_the_clip_hidden_and_manual_override_publishes_it(hype_env):
    anchor = time.time() - 60
    play = mlb_play(anchor - 20, text='Hoerner grounded out to second.')
    judge = FakeJudge()
    monitor = make_monitor(play, anchor, judge=judge)
    asyncio.run(monitor.clip_play(play))
    write_store(thread_posts(anchor, CALM_TEXTS), [{'at': anchor + 40, 'outcome': 'ok', 'entries': 100,
                                                     'oldest': anchor - 700}])
    asyncio.run(monitor.social.tick())
    event_id = clip_id(monitor.game, play['play_id'])
    status = monitor.play_status(play)
    assert status.startswith('rejected: low fan hype (') and judge.calls == []  # below threshold: no LLM call
    held = json.loads(hg.pending_paths(event_id)['json'].read_text())
    assert held['pending']['decision']['status'] == 'rejected' and held['fan_hype']['status'] == 'rejected'
    assert catalog().get(event_id) is None and event_id not in published_ids()
    asyncio.run(monitor.clip_play(play))  # re-running the loop does not re-cut or publish it
    assert catalog().get(event_id) is None

    atomic_json(monitor.request_path(play), {'game_id': GAME_ID, 'play_id': play['play_id']})
    asyncio.run(monitor.clip_play(play))
    record = catalog().get(event_id)
    assert record['reasons'] == ['manual_override'] and record['fan_hype']['status'] == 'rejected'
    assert not monitor.request_path(play).exists()


def test_llm_not_viral_rejects_even_a_loud_window(hype_env):
    anchor = time.time() - 60
    play = mlb_play(anchor - 20)
    monitor = make_monitor(play, anchor, judge=FakeJudge(viral=False, hype=.1))
    asyncio.run(monitor.clip_play(play))
    write_store(thread_posts(anchor, HYPE_TEXTS), [{'at': anchor + 40, 'outcome': 'ok', 'entries': 100,
                                                     'oldest': anchor - 700}])
    asyncio.run(monitor.social.tick())
    assert monitor.play_status(play).startswith('rejected: LLM says not viral')
    assert catalog().get(clip_id(monitor.game, play['play_id'])) is None


@pytest.mark.parametrize('judge', [FakeJudge(error=httpx.ConnectError('down')), FakeJudge(hang=True),
                                   FakeJudge(error=ValueError('malformed'))])
def test_unavailable_or_hanging_llm_uses_the_strict_heuristics_bar(hype_env, monkeypatch, judge):
    monkeypatch.setattr(settings, 'social_hype_llm_timeout_seconds', .05)
    monkeypatch.setattr(hg, 'LLM_BOUND_SLACK', 0)
    anchor = time.time() - 60
    play = mlb_play(anchor - 20)
    monitor = make_monitor(play, anchor, judge=judge)
    asyncio.run(monitor.clip_play(play))
    write_store(thread_posts(anchor, HYPE_TEXTS * 2), [{'at': anchor + 40, 'outcome': 'ok', 'entries': 100,
                                                         'oldest': anchor - 700}])
    started = time.monotonic()
    asyncio.run(monitor.social.tick())
    assert time.monotonic() - started < 5
    record = catalog().get(clip_id(monitor.game, play['play_id']))
    assert record['fan_hype']['llm']['status'] == 'unavailable'
    assert record['fan_hype']['decision'].endswith('(heuristics only; LLM unavailable)')


def test_thin_first_sample_waits_for_one_more_poll_then_decides(hype_env):
    now = [time.time()]
    anchor = now[0] - 60
    play = mlb_play(anchor - 20)
    monitor = make_monitor(play, anchor)
    monitor.social.clock = lambda: now[0]
    asyncio.run(monitor.clip_play(play))
    polls = [{'at': anchor + 40, 'outcome': 'ok', 'entries': 100, 'oldest': anchor - 700}]
    write_store(thread_posts(anchor, ['OMGGGG']), polls)
    asyncio.run(monitor.social.tick())
    asyncio.run(monitor.social.tick())
    review = monitor.social.review_for(play)
    assert review['status'] == 'waiting' and review['repolled']
    assert reddit_rss.read_priority(reddit_rss.priority_path())  # asked the poller for another look
    write_store(thread_posts(anchor, ['OMGGGG']), polls + [{'at': anchor + 100, 'outcome': 'ok', 'entries': 60,
                                                             'oldest': anchor + 20}])
    now[0] += 1
    asyncio.run(monitor.social.tick())
    assert monitor.social.review_for(play)['status'] == 'rejected'


def test_cant_miss_home_run_publishes_at_cut_and_gets_fan_hype_later(hype_env):
    anchor = time.time() - 60
    play = mlb_play(anchor - 20, text='Machado homered to left (402 feet).', event_type='home-run',
                    home_score=4, away_score=2)
    judge = FakeJudge()
    monitor = make_monitor(play, anchor, judge=judge)
    asyncio.run(monitor.clip_play(play))
    event_id = clip_id(monitor.game, play['play_id'])
    record = catalog().get(event_id)
    assert record['reasons'] == ['initial_filter', 'cant_miss', 'home_run']
    assert record['fan_hype'] == {'version': 1, 'status': 'auto_pass', 'decision': 'auto-pass: home run',
                                  'cant_miss': 'home_run'}
    assert record['capture']['decision_latency_seconds'] == 0.0
    assert not hg.pending_paths(event_id)['json'].exists() and event_id in published_ids()
    assert monitor.play_view(play)['gate'] == 'auto-pass: home run' and monitor.play_status(play) == 'clipped'

    write_store(thread_posts(anchor, HYPE_TEXTS), [{'at': anchor + 40, 'outcome': 'ok', 'entries': 100,
                                                     'oldest': anchor - 700}])
    asyncio.run(monitor.social.tick())
    enriched = json.loads((settings.clips_dir / f'{event_id}.json').read_text())
    assert enriched['fan_hype']['status'] == 'auto_pass' and enriched['fan_hype']['pre_score'] >= .45
    assert enriched['fan_hype']['llm']['viral'] is True and enriched['reasons'] == record['reasons']
    assert enriched['received_utc'] == record['received_utc']  # enrichment never re-publishes


def test_league_without_reddit_coverage_publishes_at_cut(hype_env):
    anchor = time.time() - 60
    play = {'play_id': '401856700123', 'occurred': anchor - 20, 'period': 2, 'clock': '8:28', 'clock_seconds': 508,
            'text': 'Hughes 25 yard run to the HOU 40', 'interesting': True, 'home_score': 7, 'away_score': 0}
    monitor = make_monitor(play, anchor, league='ncaaf')
    asyncio.run(monitor.clip_play(play))
    record = catalog().get(clip_id(monitor.game, play['play_id']))
    assert record['reasons'] == ['initial_filter'] and record['fan_hype']['status'] == 'unavailable'
    assert monitor.social.reviews == {}


def test_gate_off_keeps_todays_behaviour(hype_env, monkeypatch):
    monkeypatch.setattr(settings, 'social_clip_gate', False)
    anchor = time.time() - 60
    play = mlb_play(anchor - 20)
    monitor = make_monitor(play, anchor)
    asyncio.run(monitor.clip_play(play))
    record = catalog().get(clip_id(monitor.game, play['play_id']))
    assert monitor.social is None and record['reasons'] == ['initial_filter'] and 'fan_hype' not in record
    assert not hg.pending_dir().exists()


def test_restart_readopts_a_held_clip_from_its_sidecar(hype_env):
    anchor = time.time() - 60
    play = mlb_play(anchor - 20)
    monitor = make_monitor(play, anchor)
    asyncio.run(monitor.clip_play(play))
    (monitor.directory / 'hype-reviews.json').unlink()
    restarted = make_monitor(play, anchor)
    asyncio.run(restarted.clip_play(play))
    review = restarted.social.review_for(play)
    assert review['status'] == 'waiting' and review['anchor'] == pytest.approx(anchor)


# --------------------------------------------------------------------------- pending cleanup

def test_pending_cleanup_removes_only_expired_rejected_held_clips(tmp_path):
    clips, pending = tmp_path / 'clips', tmp_path / 'clips-pending'
    clips.mkdir()
    pending.mkdir()
    now = time.time()
    old = now - 7 * 3600
    for name in ('published', 'old'):  # a published clip, and one sharing an id with a held clip
        for suffix in ('.mp4', '.jpg', '.json'):
            (clips / (name + suffix)).write_text('{}' if suffix == '.json' else 'data')
            os.utime(clips / (name + suffix), (old, old))

    def held(name, decision):
        for suffix in ('.mp4', '.jpg'):
            (pending / (name + suffix)).write_bytes(b'data')
            os.utime(pending / (name + suffix), (old, old))
        (pending / (name + '.json')).write_text(json.dumps({'event_id': name, 'pending': {'decision': decision}}))
    held('old', {'status': 'rejected', 'at': old})
    held('fresh', {'status': 'rejected', 'at': now - 3600})
    held('undecided', None)
    (pending / 'orphan.mp4').write_bytes(b'data')
    os.utime(pending / 'orphan.mp4', (old, old))

    removed = hg.cleanup_pending(pending, 6 * 3600, now, clips)
    assert sorted(removed) == ['old', 'orphan.mp4']
    assert sorted(p.name for p in pending.iterdir()) == sorted(
        f'{n}{s}' for n in ('fresh', 'undecided') for s in ('.mp4', '.jpg', '.json'))
    assert sorted(p.name for p in clips.iterdir()) == sorted(
        f'{n}{s}' for n in ('published', 'old') for s in ('.mp4', '.jpg', '.json'))
    with pytest.raises(ValueError):
        hg.cleanup_pending(clips, 0, now, clips)


# --------------------------------------------------------------------------- priority polling (server side)

class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


class Reader:
    def __init__(self, clock):
        self.limiter = RateLimiter(min_interval=30, clock=clock, sleep=clock.sleep)
        self.calls = []

    async def comments(self, thread):
        self.calls.append(thread['subreddit'])

        async def send():
            return httpx.Response(200)
        await self.limiter.request(send)
        link = f"https://www.reddit.com/r/{thread['subreddit']}/comments/{thread['id']}/slug/abc{len(self.calls)}/"
        return 'ok', {'entries': [{'id': f't1_abc{len(self.calls)}', 'link': link, 'author': 'someone',
                                   'updated': '2026-09-30T04:52:10+00:00', 'content': 'OMGGGG'}]}


def test_priority_request_takes_the_next_limiter_slot_for_the_busiest_thread(tmp_path):
    clock = Clock()
    base = 1790744000.0
    wall = lambda: base + clock.now  # noqa: E731
    poller = RedditRSSPoller(Reader(clock), scoreboard=None, state_path=tmp_path / 'reddit.json', clock=wall,
                             captured=set, sleep=clock.sleep)
    poller.loaded, poller.last_scoreboard = True, wall()
    padres = dict(THREAD, polled_at=wall() - 10)
    cubs = {'id': 'cubs1', 'kind': 'game_thread', 'subreddit': 'CHICubs', 'title': 'Game Thread: Cubs @ Padres',
            'url': 'https://www.reddit.com/r/CHICubs/comments/cubs1/game_thread/', 'polled_at': wall() - 100}
    busy = {f'reddit:t1_x{i}': {'id': f'reddit:t1_x{i}', 'created_at': iso(wall() - i), 'thread_url': THREAD['url']}
            for i in range(5)}
    poller.games = {GAME_ID: {'game': FIXTURE['game'] | {'status': 'in'}, 'status': 'in', 'last_live': wall(),
                              'threads': {'padres:game_thread': padres, 'chicubs:game_thread': cubs}, 'posts': busy}}
    for sub in ('padres', 'chicubs', 'baseball'):
        poller.listings[sub] = {'at': wall(), 'outcome': 'ok'}
    assert poller.next_task()[2] is cubs  # round robin: the thread polled longest ago

    reddit_rss.request_priority(f'{GAME_ID}:1', GAME_ID, 'mlb', not_before=wall() + 15, until=wall() + 200,
                                path=poller.priority_path, now=wall())
    asyncio.run(poller.step())
    assert poller.reader.calls == ['Padres'] and clock.sleeps == [5, 5, 5]  # held the free slot until due
    saved = json.loads((tmp_path / 'reddit.json').read_text())
    log = saved['games'][GAME_ID]['threads']['padres:game_thread']['polls']
    assert log[-1]['outcome'] == 'ok' and log[-1]['entries'] == 1  # persisted even for a small batch
    assert poller.due_priority() is None  # met: polled after not_before
    asyncio.run(poller.step())
    assert poller.reader.calls == ['Padres', 'CHICubs'] and clock.sleeps[-1] == 30  # back to the shared pace


def test_switching_the_gate_off_publishes_held_clips_instead_of_stranding_them(hype_env, monkeypatch):
    anchor = time.time() - 60
    play = mlb_play(anchor - 20)
    monitor = make_monitor(play, anchor)
    asyncio.run(monitor.clip_play(play))
    event_id = clip_id(monitor.game, play['play_id'])
    assert hg.pending_paths(event_id)['json'].exists()
    monkeypatch.setattr(settings, 'social_clip_gate', 'off')
    restarted = make_monitor(play, anchor)
    asyncio.run(restarted.clip_play(play))
    assert catalog().get(event_id)['reasons'] == ['initial_filter']
    assert not hg.pending_paths(event_id)['json'].exists()
