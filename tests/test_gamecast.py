"""Gamecast data layer, tested against trimmed real ESPN responses from 2026-09-29 / NFL Week 3."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from bigplays.ingest import gamecast

FIXTURES = Path(__file__).parent / 'fixtures' / 'gamecast'
GAME_KEYS = {'game_id', 'league', 'status', 'status_detail', 'start_utc', 'venue', 'broadcast', 'period',
             'period_label', 'clock', 'away', 'home', 'situation', 'last_play_text', 'clip_count', 'viral_count'}
TEAM_KEYS = {'id', 'abbr', 'name', 'short_name', 'logo', 'color', 'alt_color', 'score', 'record', 'winner'}
PLAY_KEYS = {'play_id', 'sequence', 'period', 'period_label', 'clock', 'text', 'type', 'scoring', 'team_abbr',
             'away_score', 'home_score', 'wallclock_utc', 'is_key_play', 'mlb', 'clip', 'viral', 'viral_reason'}


def load(name):
    return json.loads((FIXTURES / name).read_text())


def mlb_board():
    return {g['game_id']: g for g in gamecast.parse_scoreboard('mlb', load('mlb_scoreboard_20260929.json'))}


def test_mlb_scoreboard_live_final_and_scheduled_games():
    games = mlb_board()
    assert set(games) >= {'401907924', '401907965', '401907974'}
    for game in games.values():
        assert set(game) == GAME_KEYS
        assert set(game['away']) == set(game['home']) == TEAM_KEYS
        assert game['clip_count'] == 0 and game['viral_count'] == 0

    live = games['401907924']
    assert (live['status'], live['status_detail'], live['period'], live['period_label']) == ('in', 'Bot 2nd', 2, 'Bottom 2')
    assert live['away']['abbr'] == 'BOS' and live['home']['abbr'] == 'NYY'
    assert live['home']['logo'].startswith('https://a.espncdn.com/') and live['home']['color'] == '#132448'
    assert (live['away']['score'], live['home']['score'], live['home']['winner']) == (0, 1, None)
    assert live['venue'] == 'Yankee Stadium' and live['broadcast'] == 'NBC'
    assert live['start_utc'] == '2026-09-30T00:00:00Z'
    assert live['situation'] == {'balls': 1, 'strikes': 2, 'outs': 2, 'on_first': True, 'on_second': True,
                                 'on_third': False, 'batter': 'Paul Goldschmidt', 'pitcher': 'Payton Tolle'}
    assert live['clock'] is None

    final = games['401907965']
    assert final['status'] == 'post' and final['status_detail'] == 'Final' and final['situation'] is None
    assert (final['away']['abbr'], final['away']['score'], final['away']['winner']) == ('PHI', 3, False)
    assert (final['home']['abbr'], final['home']['score'], final['home']['winner']) == ('ATL', 5, True)

    pre = games['401907974']
    assert pre['status'] == 'pre' and pre['period'] is None and pre['period_label'] is None
    assert pre['away']['score'] is None and pre['home']['score'] is None
    assert pre['away']['record'] == '89-73' and pre['start_utc'] == '2026-09-30T02:00:00Z'


def test_mlb_final_game_collapses_pitches_into_at_bats():
    detail = gamecast.parse_game('mlb', load('mlb_summary_401907965.json'))
    game, linescore, plays = detail['game'], detail['linescore'], detail['plays']
    assert game['status'] == 'post' and game['period'] == 9
    assert [p['away'] for p in linescore['periods']] == [1, 0, 0, 0, 0, 0, 2, 0, 0]
    assert [p['home'] for p in linescore['periods']] == [0, 1, 0, 0, 0, 1, 0, 3, None]  # bottom 9 not played
    assert linescore['totals'] == {'away': {'R': 3, 'H': 6, 'E': 0}, 'home': {'R': 5, 'H': 7, 'E': 0}}

    for play in plays:
        assert PLAY_KEYS <= set(play) and play['clip'] is None and play['viral'] is False
    assert [p['sequence'] for p in plays] == sorted(p['sequence'] for p in plays)

    first = plays[0]
    assert first['play_id'] == '4019079650001990057'  # ESPN's play-result id, same as parse_mlb_plays
    assert first['text'] == 'Turner struck out swinging.' and first['type'] == 'Strikeout'
    assert first['period_label'] == 'Top 1' and first['team_abbr'] == 'PHI'
    assert first['mlb'] == {'batter': 'Trea Turner', 'pitcher': 'Chris Sale', 'balls': 1, 'strikes': 2,
                            'outs': 1, 'pitch_count': 4, 'pitcher_pitch_count': 4}
    assert '4019079650001050037' in first['related_play_ids']  # the strike-three pitch
    assert first['wallclock_utc'] == '2026-09-29T18:17:29Z'

    bohm = next(p for p in plays if p['play_id'] == '4019079650004990057')
    assert bohm['scoring'] and bohm['is_key_play'] and (bohm['away_score'], bohm['home_score']) == (1, 0)

    subs = [p for p in plays if p['type'] == 'Substitution']
    assert any(p['text'] == 'Iglesias relieved Hicklen' for p in subs) and all(p['mlb'] is None for p in subs)

    # Inning-ending pickoff: the event is its own play and keeps the interrupted at-bat's pitch.
    pickoff = next(p for p in plays if p['play_id'] == '4019079650404020298')
    assert pickoff['related_play_ids'] == ['4019079650404010001', '4019079650404020037',
                                           '4019079650404020277', '4019079650404020298']

    # Every pitch belongs to exactly one play.
    related = [i for p in plays for i in p['related_play_ids']]
    assert len(related) == len(set(related))
    pitch_ids = {p['id'] for p in load('mlb_summary_401907965.json')['plays'] if p.get('summaryType') == 'P'}
    assert pitch_ids <= set(related)
    assert game['last_play_text'] == 'Stott flied out to left.'


def test_mlb_live_game_uses_situation_and_skips_in_progress_at_bat():
    detail = gamecast.parse_game('mlb', load('mlb_summary_401907924.json'))
    game, plays = detail['game'], detail['plays']
    assert game['status'] == 'in' and game['period_label'] == 'Bottom 2'
    assert game['situation']['batter'] == 'Paul Goldschmidt' and game['situation']['pitcher'] == 'Payton Tolle'
    assert game['situation']['on_first'] and not game['situation']['on_third']
    assert detail['linescore']['totals']['home'] == {'R': 1, 'H': 4, 'E': 0}
    assert len(detail['linescore']['periods']) == 9 and detail['linescore']['periods'][2]['away'] is None
    last = plays[-1]
    assert last['text'].startswith('Wells singled to right') and last['scoring'] and last['home_score'] == 1
    # The current at-bat (pitches 4019079240306...) has no result yet, so it is not a play.
    assert not any(p['play_id'].startswith('4019079240306') for p in plays)


def test_mlb_scheduled_game_has_no_plays():
    detail = gamecast.parse_game('mlb', load('mlb_summary_401907974.json'))
    assert detail['game']['status'] == 'pre' and detail['plays'] == []
    assert detail['game']['away']['abbr'] == 'CHC' and detail['game']['home']['abbr'] == 'SD'
    assert detail['linescore']['totals']['away'] == {'R': None, 'H': None, 'E': None}


def test_nfl_scoreboard_and_game_detail():
    board = gamecast.parse_scoreboard('nfl', load('nfl_scoreboard_20260924.json'))
    game = next(g for g in board if g['game_id'] == '401872948')
    assert (game['away']['abbr'], game['away']['score'], game['away']['winner']) == ('ATL', 35, True)
    assert (game['home']['abbr'], game['home']['score'], game['home']['winner']) == ('GB', 14, False)
    assert game['status'] == 'post' and game['venue'] == 'Lambeau Field'

    detail = gamecast.parse_game('nfl', load('nfl_summary_401872948.json'))
    plays = detail['plays']
    assert detail['linescore']['periods'] == [{'label': '1', 'away': 7, 'home': 7}, {'label': '2', 'away': 10, 'home': 0},
                                              {'label': '3', 'away': 7, 'home': 0}, {'label': '4', 'away': 11, 'home': 7}]
    assert detail['linescore']['totals'] == {'away': {'score': 35}, 'home': {'score': 14}}
    assert len(plays) == 184 and len({p['play_id'] for p in plays}) == 184
    wallclocks = [p['wallclock_utc'] for p in plays]
    assert wallclocks == sorted(wallclocks)

    td = next(p for p in plays if p['play_id'] == '401872948682')  # id used by the week-3 clip catalog
    assert td['scoring'] and td['is_key_play'] and td['period_label'] == 'Q1' and td['clock'] == '5:13'
    assert (td['away_score'], td['home_score'], td['team_abbr']) == (0, 7, 'GB')
    assert td['mlb'] is None and td['clip'] is None

    rush = next(p for p in plays if p['play_id'] == '40187294863')
    assert rush['type'] == 'Rush' and rush['team_abbr'] == 'ATL' and not rush['is_key_play']
    assert rush['nfl']['down_distance_text'] == '1st & 10 at ATL 30'
    interception = next(p for p in plays if p['play_id'] == '401872948133')
    assert interception['is_key_play']  # turnover
    assert detail['game']['period'] == 4 and detail['game']['situation'] is None


def test_nfl_live_situation_shape():
    # Synthetic, in ESPN's scoreboard shape (no NFL game was live on validation day).
    payload = load('nfl_scoreboard_20260924.json')
    event = payload['events'][0]
    event['status'] = {'period': 3, 'displayClock': '5:13',
                       'type': {'state': 'in', 'shortDetail': '5:13 - 3rd'}}
    event['competitions'][0]['situation'] = {'down': 3, 'distance': 4, 'yardLine': 35, 'possession': '9',
                                             'possessionText': 'GB 35', 'isRedZone': False,
                                             'downDistanceText': '3rd & 4 at GB 35', 'lastPlay': {'text': 'J.Love pass'}}
    game = gamecast.parse_scoreboard('nfl', payload)[0]
    assert (game['period'], game['period_label'], game['clock']) == (3, 'Q3', '5:13')
    assert game['situation'] == {'down': 3, 'distance': 4, 'yard_line_text': 'GB 35', 'possession': 'GB',
                                 'is_red_zone': False, 'down_distance_text': '3rd & 4 at GB 35'}
    assert game['last_play_text'] == 'J.Love pass' and game['home']['winner'] is None
    assert gamecast.nfl_period_label(5) == 'OT' and gamecast.nfl_period_label(6) == '2OT'


# ------------------------------------------------------------------ fetch + cache

@pytest.fixture
def fake_espn(monkeypatch):
    gamecast.clear_cache()
    calls = []
    state = {'fail': False, 'delay': 0.0}

    async def fetch(url, params=None):
        calls.append((url, dict(params or {})))
        await asyncio.sleep(state['delay'])
        if state['fail']:
            raise RuntimeError('ESPN down')
        if url.endswith('/baseball/mlb/scoreboard'):
            return load('mlb_scoreboard_20260929.json')
        if url.endswith('/football/nfl/scoreboard'):
            return load('nfl_scoreboard_20260924.json')
        league = 'mlb' if '/baseball/' in url else 'nfl'
        return load(f"{league}_summary_{params['event']}.json")

    monkeypatch.setattr(gamecast, 'fetch_json', fetch)
    yield calls, state
    gamecast.clear_cache()


def test_fetch_scoreboard_caches_and_defaults_to_la_today(fake_espn, monkeypatch):
    calls, _ = fake_espn
    monkeypatch.setattr(gamecast, 'today_local', lambda: '20260929')

    async def run():
        first = await gamecast.fetch_scoreboard('mlb')
        second = await gamecast.fetch_scoreboard('mlb', '20260929')
        return first, second

    first, second = asyncio.run(run())
    assert first == second and len(calls) == 1
    assert calls[0] == ('https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard', {'dates': '20260929'})


def test_concurrent_requests_share_one_fetch(fake_espn):
    calls, state = fake_espn
    state['delay'] = 0.05

    async def run():
        return await asyncio.gather(*(gamecast.fetch_game('mlb', '401907965') for _ in range(5)))

    results = asyncio.run(run())
    assert len(calls) == 1 and all(r == results[0] for r in results)
    assert results[0]['plays'][0]['play_id'] == '4019079650001990057'


def test_summary_ttl_depends_on_status_and_stale_value_served_on_error(fake_espn, monkeypatch):
    calls, state = fake_espn
    now = [1000.0]
    monkeypatch.setattr(gamecast.time, 'monotonic', lambda: now[0])

    async def run():
        live = await gamecast.fetch_game('mlb', '401907924')
        final = await gamecast.fetch_game('mlb', '401907965')
        now[0] += 9  # past the live TTL, within the final TTL
        await gamecast.fetch_game('mlb', '401907924')
        await gamecast.fetch_game('mlb', '401907965')
        assert len(calls) == 3
        now[0] += 60
        state['fail'] = True
        stale = await gamecast.fetch_game('mlb', '401907924')  # served from last good value
        assert stale['game']['game_id'] == live['game']['game_id']
        with pytest.raises(gamecast.GamecastError):
            await gamecast.fetch_game('mlb', '401907974')  # never fetched: nothing to fall back to
        return final

    final = asyncio.run(run())
    assert final['game']['status'] == 'post'


def test_fetch_game_prefers_cached_scoreboard_situation(fake_espn):
    async def run():
        await gamecast.fetch_scoreboard('mlb', '20260929')
        return await gamecast.fetch_game('mlb', '401907924')

    detail = asyncio.run(run())
    assert detail['game']['situation']['batter'] == 'Paul Goldschmidt'


def test_invalid_arguments():
    with pytest.raises(ValueError):
        asyncio.run(gamecast.fetch_scoreboard('nba'))
    with pytest.raises(ValueError):
        asyncio.run(gamecast.fetch_scoreboard('mlb', '2026-09-29'))
    with pytest.raises(ValueError):
        asyncio.run(gamecast.fetch_game('mlb', '../etc'))


@pytest.mark.skipif(not os.environ.get('GAMECAST_LIVE'), reason='set GAMECAST_LIVE=1 to hit ESPN')
def test_live_espn_smoke():
    gamecast.clear_cache()

    async def run():
        games = await gamecast.fetch_scoreboard('mlb', '20260929')
        detail = await gamecast.fetch_game('nfl', '401872948')
        return games, detail

    games, detail = asyncio.run(run())
    assert games and detail['plays']
