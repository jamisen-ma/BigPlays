"""Reddit public RSS game-thread reactions: parsing, discovery, rate limiting, matching.

Fixtures in tests/fixtures/reddit/ are trimmed real Atom responses from tonight's
Cubs @ Padres game thread with every username replaced (fanN / mlb-bot). Three
rows marked ``<!-- synthetic -->`` exercise filters the real sample lacked.
"""
import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bigplays.config import settings
from bigplays.ingest import reddit_rss
from bigplays.ingest.reddit_rss import (RateLimiter, RedditRSSPoller, find_threads, match_thread, normalize_comment,
                                        parse_atom, thread_kind, title_dates)
from bigplays.orchestrator.social_ranker import SocialEnricher, classify_posts, clip_terms, match_post
from bigplays.storage.catalog import catalog_for

FIXTURES = Path(__file__).parent / 'fixtures' / 'reddit'
LISTING = (FIXTURES / 'padres_new.rss').read_text()
COMMENTS = (FIXTURES / 'game_thread_comments.rss').read_text()
THREAD_URL = 'https://www.reddit.com/r/Padres/comments/1wtovip/game_thread_cubs_at_padres_29_sep_2026_700pm_pdt/'

GAME = {'game_id': '401907974', 'league': 'mlb', 'status': 'in', 'start_utc': '2026-09-30T02:00:00Z',
        'home': {'abbr': 'SD', 'name': 'San Diego Padres', 'short_name': 'Padres'},
        'away': {'abbr': 'CHC', 'name': 'Chicago Cubs', 'short_name': 'Cubs'}}
THREAD = {'id': '1wtovip', 'kind': 'game_thread', 'subreddit': 'Padres', 'url': THREAD_URL,
          'title': 'Game Thread: Cubs at Padres - 29 Sep 2026 - 7:00PM PDT', 'game_id': '401907974', 'league': 'mlb'}


def clip(event_id, occurred, title, **extra):
    """Shaped like the live_capture records in data/clips.sqlite3 (no player/home/away fields)."""
    return {'event_id': event_id, 'game_id': '401907974', 'league': 'mlb', 'source_kind': 'live_capture',
            'name': 'Chicago Cubs at San Diego Padres', 'occurred_utc': occurred, 'title': title,
            'description': title, 'tags': ['play', 'clock-aligned'], 'reasons': ['initial_filter'],
            'base_score': .8, 'combined_score': .8, 'file': f'{event_id}.mp4', **extra}


SUZUKI = clip('clip-suzuki', '2026-09-30T04:43:04+00:00', 'Suzuki singled to center.')
CRONENWORTH = clip('clip-cronenworth', '2026-09-30T04:37:16+00:00', 'Cronenworth singled to left, Bogaerts to second.')


def comment_posts():
    feed = parse_atom(COMMENTS)
    return [p for e in feed['entries'] if (p := normalize_comment(e, THREAD, '2026-09-30T04:44:02Z'))]


# --------------------------------------------------------------------------- parsing

def test_parse_comment_feed_and_normalize_to_post_shape():
    feed = parse_atom(COMMENTS)
    assert feed['title'].startswith('Game Thread: Cubs at Padres')
    assert feed['entries'][0]['id'] == 't3_1wtovip'  # the thread post comes first
    assert all(e['id'].startswith('t1_') for e in feed['entries'][1:])
    posts = comment_posts()
    ids = [p['id'] for p in posts]
    assert 'reddit:t3_1wtovip' not in ids                      # bot box score is not a reaction
    assert 'reddit:t1_synth02' not in ids                      # AutoModerator
    assert 'reddit:t1_synth03' not in ids                      # [deleted]
    assert len(posts) == len(feed['entries']) - 3
    suzuki = next(p for p in posts if 'Suzuki' in p['text'])
    assert suzuki == {**suzuki, 'id': 'reddit:t1_pcxuodj', 'provider': 'reddit',
                      'text': 'I think the only Cub to show up was Suzuki.',
                      'created_at': '2026-09-30T04:43:47Z', 'metrics': {},
                      'url': THREAD_URL + 'pcxuodj/', 'game_id': '401907974', 'thread_kind': 'game_thread'}
    assert re.fullmatch(r'u/fan\d+', suzuki['author_display_name'])
    assert re.fullmatch(r'[0-9a-f]{64}', suzuki['author_key'])
    assert len({p['author_key'] for p in posts}) == len(posts)


def test_html_is_stripped_to_plain_text():
    posts = {p['id']: p for p in comment_posts()}
    assert posts['reddit:t1_synth01']['text'] == 'Cronenworth & Bogaerts raking tonight.\nLETS GO PADRES!!! 🔥'
    giphy = posts['reddit:t1_pcxud2j']['text']
    assert giphy == 'Java Joe in the playoffs again\nhttps://giphy.com/gifs/y55RKbourfozc3IOtS'
    assert not any('<' in p['text'] or '&lt;' in p['text'] or 'SC_OFF' in p['text'] for p in posts.values())


def test_parser_rejects_dtds_and_non_atom():
    with pytest.raises(ValueError):
        parse_atom('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><feed xmlns="http://www.w3.org/2005/Atom"/>')
    with pytest.raises(ValueError):
        parse_atom('<rss><channel/></rss>')


# --------------------------------------------------------------------------- discovery

def test_discovers_the_game_thread_by_title_date_and_teams():
    entries = parse_atom(LISTING)['entries']
    found = find_threads(entries, GAME, 'Padres')
    assert set(found) == {'game_thread'}  # pre-game, off-day and Sep 27 post-game threads are not it
    assert found['game_thread']['url'] == THREAD_URL and found['game_thread']['id'] == '1wtovip'
    # Tomorrow's game in the same series must not reuse tonight's "29 Sep" thread.
    tomorrow = {**GAME, 'game_id': '401907999', 'start_utc': '2026-10-01T01:10:00Z'}
    assert find_threads(entries, tomorrow, 'Padres') == {}
    # A different opponent never matches.
    dodgers = {**GAME, 'away': {'abbr': 'LAD', 'name': 'Los Angeles Dodgers', 'short_name': 'Dodgers'}}
    assert find_threads(entries, dodgers, 'Padres') == {}


def entry(title, published, tid='abc123', sub='baseball'):
    return {'id': f't3_{tid}', 'title': title, 'published': published, 'updated': published,
            'link': f'https://www.reddit.com/r/{sub}/comments/{tid}/some_slug/'}


def test_league_subreddit_needs_both_teams_and_post_game_threads_come_after_first_pitch():
    both = entry('Game Thread: Chicago Cubs (92-70) @ San Diego Padres (90-72) - 7:10 PM PT', '2026-09-30T01:00:00Z')
    assert match_thread(both, GAME, 'baseball') == 'game_thread'
    assert match_thread(entry('Game Thread: Dodgers @ Padres', '2026-09-30T01:00:00Z'), GAME, 'baseball') is None
    # In a team's own subreddit only the opponent must be named.
    assert match_thread(entry('Game Thread: vs Cubs', '2026-09-30T01:00:00Z', sub='Padres'), GAME, 'Padres') == 'game_thread'
    pgt = entry('Post Game Thread: The Padres beat the Cubs 8-0 - 29 Sep 2026', '2026-09-30T05:05:00Z', 'pgt1')
    assert match_thread(pgt, GAME, 'baseball') == 'post_game_thread'
    early_pgt = entry('Post Game Thread: The Padres beat the Cubs', '2026-09-30T01:00:00Z', 'pgt0')
    assert match_thread(early_pgt, GAME, 'baseball') is None  # a PGT before first pitch is an older game's
    assert match_thread({**both, 'link': 'https://evil.example/r/baseball/comments/abc123/x/'}, GAME, 'baseball') is None
    assert thread_kind('Pre-Game Thread: Cubs at Padres') is None
    assert thread_kind('Off-Day Thread - 28 Sep 2026') is None
    assert thread_kind('Post-Game Thread: The Padres pulled away') == 'post_game_thread'
    assert thread_kind('Game Day Thread: Bears at Packers') == 'game_thread'
    assert title_dates('Game Thread: Cubs at Padres - 29 Sep 2026 - 7:00PM PDT', 2026) == {__import__('datetime').date(2026, 9, 29)}


def test_every_team_has_a_subreddit():
    from bigplays.orchestrator.social_ranker import TEAM_ALIASES
    for league in ('mlb', 'nfl'):
        teams = {reddit_rss.TEAM_SUBREDDITS[league][code] for code in TEAM_ALIASES[league]}
        assert len(teams) == {'mlb': 30, 'nfl': 32}[league]
        assert all(reddit_rss.SUBREDDIT.fullmatch(s) for s in teams)


# --------------------------------------------------------------------------- rate limiter

class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def response(code=200, **headers):
    return httpx.Response(code, headers={k.replace('_', '-'): str(v) for k, v in headers.items()}, text='')


def test_limiter_spaces_requests_and_honors_rate_limit_headers(tmp_path):
    clock = FakeClock()
    limiter = RateLimiter(min_interval=30, max_backoff=900, clock=clock, sleep=clock.sleep,
                          state_path=tmp_path / 'limiter.json', wall=clock)
    sent = []
    replies = iter([
        response(200, x_ratelimit_used=1, x_ratelimit_remaining='0.0', x_ratelimit_reset=56),  # -> wait 57
        response(200, x_ratelimit_remaining=50, x_ratelimit_reset=100),                         # -> min 30
        response(429, retry_after=120),                                                          # -> 120
        response(429),                                                                           # -> 2nd failure 120
        response(429),                                                                           # -> 240
        response(503),                                                                           # -> 480
        response(200),                                                                           # success resets
        response(200),
    ])

    async def send():
        sent.append(clock.now)
        return next(replies)

    async def run():
        for _ in range(8):
            await limiter.request(send)
    asyncio.run(run())
    gaps = [round(b - a, 3) for a, b in zip(sent, sent[1:])]
    assert gaps == [57, 30, 120, 120, 240, 480, 30]
    assert limiter.failures == 0 and limiter.last['outcome'] == 'ok'

    # A restarted process (new limiter, same state file) keeps honoring the window.
    clock.now = sent[-1] + 10
    again = RateLimiter(min_interval=30, clock=clock, sleep=clock.sleep, state_path=tmp_path / 'limiter.json', wall=clock)
    assert again.wait_seconds() == pytest.approx(20)


def test_limiter_backoff_caps_and_transport_errors_count():
    clock = FakeClock()
    limiter = RateLimiter(min_interval=30, max_backoff=900, clock=clock, sleep=clock.sleep)
    delays = [limiter.record(response(429)) for _ in range(7)]
    assert delays == [60, 120, 240, 480, 900, 900, 900]
    assert limiter.record(response(200, x_ratelimit_remaining='0.0', x_ratelimit_reset=300)) == 301
    assert limiter.record(None) == 60  # network error after a success: first backoff step
    assert limiter.record(response(404)) == 30 and limiter.last['outcome'] == 'not_found'


def test_limiter_is_one_queue_and_never_bursts():
    clock = FakeClock()
    limiter = RateLimiter(min_interval=30, clock=clock, sleep=clock.sleep)
    sent = []

    async def send():
        sent.append(clock.now)
        await asyncio.sleep(0)
        return response(200)

    async def run():
        await asyncio.gather(*(limiter.request(send) for _ in range(4)))
    asyncio.run(run())
    assert [b - a for a, b in zip(sent, sent[1:])] == [30, 30, 30]


# --------------------------------------------------------------------------- poller

class FakeReader:
    def __init__(self, clock, outcomes=None, limiter=None):
        self.limiter = limiter or RateLimiter(min_interval=30, clock=clock, sleep=clock.sleep)
        self.user_agent = reddit_rss.DEFAULT_USER_AGENT
        self.calls = []
        self.outcomes = outcomes or {}

    async def _fetch(self, key, text):
        async def send():
            return response(self.outcomes.get(key, 200))
        await self.limiter.request(send)
        self.calls.append(key)
        outcome = self.limiter.last['outcome']
        return (outcome, parse_atom(text)) if outcome == 'ok' else (outcome, None)

    async def listing(self, subreddit):
        text = LISTING if subreddit == 'Padres' else '<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
        return await self._fetch('listing:' + subreddit, text)

    async def comments(self, thread):
        return await self._fetch('comments:' + thread['subreddit'], COMMENTS)

    async def close(self):
        pass


def make_poller(tmp_path, clock, reader=None, games=(GAME,)):
    async def scoreboard():
        return list(games)
    return RedditRSSPoller(reader or FakeReader(clock), scoreboard=scoreboard, state_path=tmp_path / 'reddit.json',
                           clock=lambda: 1790740000.0 + clock.now, captured=lambda: {'401907974'},
                           max_games=3, max_threads=3)


def test_poller_discovers_then_polls_dedupes_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'reddit_rss_enabled', True)
    clock = FakeClock()
    poller = make_poller(tmp_path, clock)
    heard = []
    poller.listeners.append(lambda league, game_id, new: heard.append((league, game_id, len(new))))
    assert poller.status()['status'] == 'not_checked'

    async def run(steps):
        for _ in range(steps):
            await poller.step()
    asyncio.run(run(5))
    # Home sub, away sub, league sub are each read once; then the one found thread is polled.
    assert poller.reader.calls == ['listing:Padres', 'listing:CHICubs', 'listing:baseball',
                                   'comments:Padres', 'comments:Padres']
    assert clock.sleeps == [30, 30, 30, 30]  # never faster than the minimum spacing
    posts = poller.posts('mlb')
    first_batch = len(comment_posts())
    assert len(posts) == first_batch and len({p['id'] for p in posts}) == first_batch  # 2nd poll: all duplicates
    assert heard == [('mlb', '401907974', first_batch)]
    status = poller.status('mlb')
    assert status['status'] == 'ok' and status['used_in_feed'] is True and status['threads'][0]['url'] == THREAD_URL
    assert poller.status('nfl')['status'] == 'no_live_threads'

    reloaded = make_poller(tmp_path, clock)
    reloaded.load()
    assert {p['id'] for p in reloaded.posts('mlb')} == {p['id'] for p in posts}
    saved = json.loads((tmp_path / 'reddit.json').read_text())
    assert saved['games']['401907974']['threads']['padres:game_thread']['url'] == THREAD_URL


def test_poller_reports_rate_limiting_and_backs_off(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'reddit_rss_enabled', True)
    clock = FakeClock()
    reader = FakeReader(clock, outcomes={'listing:Padres': 429})
    poller = make_poller(tmp_path, clock, reader)
    asyncio.run(poller.step())
    assert poller.status()['status'] == 'rate_limited' and poller.status()['retry_after_seconds'] == 60
    assert reader.limiter.wait_seconds() == 60
    idle = make_poller(tmp_path / 'idle', FakeClock(), games=())
    asyncio.run(idle.step())
    assert idle.checked_at is None and idle.status()['status'] == 'not_checked'
    idle.checked_at = 'now'
    assert idle.status()['status'] == 'no_live_threads'
    monkeypatch.setattr(settings, 'reddit_rss_enabled', False)
    assert idle.status()['status'] == 'disabled' and idle.status()['used_in_feed'] is False


# --------------------------------------------------------------------------- relevance

def test_game_thread_comments_become_play_evidence():
    posts = comment_posts()
    labeled = {p['id']: p for p in classify_posts(posts, [SUZUKI, CRONENWORTH])}
    suzuki = labeled['reddit:t1_pcxuodj']
    assert suzuki['relevance'] == 'play_evidence'
    match = suzuki['play_matches'][0]
    assert match['clip_id'] == 'clip-suzuki' and match['players'] == ['Suzuki']
    assert match['confidence'] == 'high' and match['context'] == 'game_thread'  # thread supplies the team
    assert match['seconds_after_play'] == 43 and match['thread_url'] == THREAD_URL
    hype = labeled['reddit:t1_synth01']  # names both runners + "LETS GO PADRES!!! 🔥", 84s after the single
    assert hype['play_matches'][0]['clip_id'] == 'clip-cronenworth'
    padres_only = next(p for p in labeled.values() if p['text'].startswith('Everything seems to be coming together'))
    assert padres_only['relevance'] == 'general_chatter'  # team without a reaction or player
    evidence = [p for p in labeled.values() if p['relevance'] == 'play_evidence']
    assert {m['clip_id'] for p in evidence for m in p['play_matches']} == {'clip-suzuki', 'clip-cronenworth'}


def test_game_thread_rules_window_team_reaction_and_other_games():
    post = lambda text, at, game='401907974': {'id': 'reddit:t1_x', 'provider': 'reddit', 'text': text,
                                               'created_at': at, 'game_id': game, 'thread_url': THREAD_URL}
    assert match_post(post('Suzuki!', '2026-09-30T04:47:30Z'), SUZUKI)['confidence'] == 'high'   # 4m26s
    assert match_post(post('Suzuki!', '2026-09-30T04:48:30Z'), SUZUKI) is None                  # > 5 min
    assert match_post(post('Suzuki!', '2026-09-30T04:42:30Z'), SUZUKI) is not None              # stream-delay slack
    assert match_post(post('Suzuki!', '2026-09-30T04:41:30Z'), SUZUKI) is None                  # well before
    assert match_post(post('Suzuki!', '2026-09-30T04:43:30Z', game='401907999'), SUZUKI) is None  # other game
    team_react = match_post(post('LFG PADRES 🔥', '2026-09-30T04:44:00Z'), SUZUKI)
    assert team_react['confidence'] == 'medium' and team_react['teams'] == ['SD'] and team_react['reaction_terms']
    assert match_post(post('Padres are cooking dinner', '2026-09-30T04:44:00Z'), SUZUKI) is None
    conforto = clip('clip-conforto', '2026-09-30T04:51:42+00:00', 'Conforto struck out looking.')
    cheer = match_post(post('Wooo!!!! LFGSD!', '2026-09-30T04:52:54Z'), conforto)  # real final-out reaction
    assert cheer['teams'] == ['SD'] and cheer['reaction_terms'] == ['lfgsd'] and cheer['confidence'] == 'medium'
    assert match_post(post('lfg cubbies', '2026-09-30T04:52:54Z'), conforto) is None
    assert match_post(post('LFGSD', '2026-09-30T04:43:00Z'), SUZUKI) is None  # a nameless cheer needs delta >= 0
    # Between two plays, a nameless cheer is attributed only to the latest one before it.
    [labeled] = classify_posts([post('LFGSD!!!', '2026-09-30T04:45:41Z')], [CRONENWORTH, SUZUKI])
    assert [m['clip_id'] for m in labeled['play_matches']] == ['clip-suzuki']
    assert match_post(post('what a play!!', '2026-09-30T04:44:00Z'), SUZUKI) is None  # no player, no team
    # Public-feed posts (no game thread) keep the original 3 h lexical rules.
    mastodon = {'id': 'mastodon:1', 'provider': 'mastodon', 'text': 'Suzuki with a single for the Cubs',
                'created_at': '2026-09-30T04:50:00Z'}  # 7 min: outside the game-thread window
    assert match_post(mastodon, SUZUKI)['confidence'] == 'high'


def test_clip_terms_read_the_batter_from_mlb_play_text():
    players = lambda title: sorted(clip_terms(clip('c', '2026-09-30T04:00:00+00:00', title))['players'].items())
    assert players('Tatis Jr. struck out swinging.') == [('Tatis Jr.', {'Tatis Jr.', 'Tatis'})]
    assert players('Merrill singled to right, Campusano scored and Machado scored, France to third.') == \
        [('Merrill', {'Merrill'})]
    assert players('Crow-Armstrong struck out swinging.') == [('Crow-Armstrong', {'Crow-Armstrong'})]
    assert players('Pitcher Change: Joe Musgrove replaces Jason Adam.') == []
    teams = clip_terms(SUZUKI)['teams']
    assert 'Padres' in teams['SD'] and 'Cubs' in teams['CHC']


# --------------------------------------------------------------------------- API wiring

@pytest.fixture
def api(monkeypatch, tmp_path):
    from bigplays.ingest import social_feeds
    from bigplays.server import social
    monkeypatch.setattr(settings, 'reddit_rss_enabled', True)
    clips = tmp_path / 'clips'
    clips.mkdir()
    monkeypatch.setattr(settings, 'clips_dir', clips)
    monkeypatch.setattr(settings, 'database_path', tmp_path / 'catalog.sqlite3')
    monkeypatch.setattr(social, '_env', lambda name: None)
    (clips / 'clip-suzuki.json').write_text(json.dumps(SUZUKI))
    (clips / 'clip-suzuki.mp4').write_bytes(b'video')
    catalog_for(clips, settings.database_path).upsert(SUZUKI)

    async def mastodon(league):
        return social_feeds.result('mastodon', 'empty', coverage='test')
    monkeypatch.setattr(social, 'feed_cache', social.FeedCache(fetch=mastodon))
    monkeypatch.setattr(social, 'enricher', SocialEnricher(social.cached_feed, clips))
    clock = FakeClock()
    poller = make_poller(tmp_path, clock)
    asyncio.run(poller.step())  # listing (finds the thread)
    for _ in range(2):
        asyncio.run(poller.step())
    asyncio.run(poller.step())  # comments
    assert poller.posts('mlb')
    monkeypatch.setattr(reddit_rss, 'poller', poller)
    app = FastAPI()
    app.include_router(social.router)
    return TestClient(app), clips


def test_feed_and_play_endpoints_include_reddit_comments(api):
    client, clips = api
    feed = client.get('/api/social/feed', params={'league': 'mlb', 'limit': 100}).json()
    reddit = [p for p in feed['posts'] if p['provider'] == 'reddit']
    assert reddit and all(p['metrics'] == {} and p['url'].startswith(THREAD_URL) for p in reddit)
    assert feed['providers']['reddit']['status'] == 'ok' and feed['providers']['reddit']['used_in_feed'] is True
    assert feed['play_evidence_count'] >= 1
    play = client.get('/api/social/play/clip-suzuki').json()
    assert [p['id'] for p in play['posts']] == ['reddit:t1_pcxuodj']
    assert play['posts'][0]['play_matches'][0]['context'] == 'game_thread'
    for _ in range(100):  # enrichment runs in the background after publication
        saved = json.loads((clips / 'clip-suzuki.json').read_text())
        if 'social_enrichment' in saved:
            break
        __import__('time').sleep(.02)
    enrichment = saved['social_enrichment']
    assert enrichment['status'] == 'matched' and enrichment['sources'][0]['provider'] == 'reddit'
    assert 'text' not in enrichment['sources'][0]  # sidecars keep links and hashes, not comment text
    assert saved['occurred_utc'] == SUZUKI['occurred_utc'] and saved['combined_score'] == .8


def test_lifespan_runs_poller_and_enriches_published_clips(monkeypatch, tmp_path):
    """New comments reach a published clip's sidecar in the background; capture never waits."""
    import time
    from bigplays.server import social
    monkeypatch.setattr(settings, 'reddit_rss_enabled', True)
    clips = tmp_path / 'clips'
    clips.mkdir()
    monkeypatch.setattr(settings, 'clips_dir', clips)
    monkeypatch.setattr(settings, 'database_path', tmp_path / 'catalog.sqlite3')
    (clips / 'clip-suzuki.json').write_text(json.dumps(SUZUKI))
    (clips / 'clip-suzuki.mp4').write_bytes(b'video')
    catalog_for(clips, settings.database_path).upsert(SUZUKI)
    monkeypatch.setattr(social, 'enricher', SocialEnricher(social.cached_feed, clips))
    reader = FakeReader(None, limiter=RateLimiter(min_interval=.01))
    started = time.monotonic()

    async def scoreboard():
        return [GAME]
    poller = RedditRSSPoller(reader, scoreboard=scoreboard, state_path=tmp_path / 'reddit.json',
                             clock=lambda: 1790740000.0 + time.monotonic() - started, captured=set)
    monkeypatch.setattr(reddit_rss, 'poller', poller)
    app = FastAPI()
    app.include_router(social.router)
    with TestClient(app):
        for _ in range(200):
            saved = json.loads((clips / 'clip-suzuki.json').read_text())
            if saved.get('social_enrichment'):
                break
            time.sleep(.02)
    assert 'comments:Padres' in reader.calls
    assert saved['social_enrichment']['sources'][0]['id'] == 'reddit:t1_pcxuodj'
    assert saved['social_enrichment']['providers']['reddit'] == 'ok'
    assert poller.listeners == []  # lifespan cleaned up
