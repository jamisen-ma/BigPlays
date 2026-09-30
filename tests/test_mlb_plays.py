from datetime import datetime, timezone

from bigplays.ingest.espn import MLB_SCOREBOARD, SCOREBOARDS, parse_games
from bigplays.ingest.plays import SUMMARY_PATHS, locate_play, parse_plays
from bigplays.models import League


def result(play_id='101', kind='home-run', text='Riley homered to right center.', inning=8):
    return {'id': play_id, 'type': {'type': 'play-result'},
            'alternativeType': {'type': kind}, 'text': text,
            'wallclock': '2026-09-29T20:35:47Z', 'period': {'number': inning, 'type': 'Bottom'},
            'pitchCount': {'balls': 0, 'strikes': 0}, 'outs': 2,
            'homeScore': 5, 'awayScore': 3, 'scoringPlay': kind == 'home-run'}


def test_mlb_routes_and_no_countdown_clock():
    assert SUMMARY_PATHS['mlb'] == 'baseball/mlb'
    assert SCOREBOARDS[League.MLB] == MLB_SCOREBOARD
    play, = parse_plays({'plays': [result()]}, 'mlb')
    assert play['occurred'] == datetime(2026, 9, 29, 20, 35, 47, tzinfo=timezone.utc).timestamp()
    assert play['period_label'] == 'Bottom 8'
    assert play['clock'] == '' and play['clock_seconds'] is None
    assert play['interesting'] and play['clutch']
    assert 'Home Run' in play['tags']
    assert locate_play(play, [{'period': 8, 'clock_seconds': 0, 'time': play['occurred']}]) is None


def test_pitch_and_result_are_one_highlight_with_pre_pitch_count():
    pitch = result('100', kind='strike-swinging')
    pitch.update(type={'type': 'strike-swinging'}, summaryType='P',
                 pitchCount={'balls': 1, 'strikes': 2}, text='Pitch 4: Strike 3 Swinging')
    completed = result('101', kind='strike-swinging', text='Turner struck out swinging.')
    completed.update(alternativePlay='100', pitchCount={'balls': 1, 'strikes': 3},
                     resultCount={'balls': 1, 'strikes': 3})
    plays = parse_plays({'plays': [pitch, completed], 'scoringPlays': [completed]}, 'mlb')
    assert len(plays) == 1
    assert plays[0]['event_type'] == 'strikeout'
    assert plays[0]['count'] == '1-2'
    assert plays[0]['result_count']['strikes'] == 3


def test_baseball_highlights_do_not_use_football_yardage():
    ordinary = result('101', kind='ground-out', text='Turner grounded out to shortstop.', inning=1)
    ordinary['statYardage'] = 99
    double_play = result('102', kind='ground-out', text='Turner grounded into double play.', inning=7)
    single = result('103', kind='single', text='Turner singled to left.', inning=1)
    scoring_walk = result('104', kind='ball', text='Turner walked, runner scored.')
    scoring_walk['scoringPlay'] = True
    plays = {p['play_id']: p for p in parse_plays({'plays': [ordinary, double_play, single, scoring_walk]}, 'mlb')}
    assert not plays['101']['interesting']
    assert all(plays[key]['interesting'] for key in ('102', '103', '104'))
    assert not plays['103']['clutch']


def test_missing_source_time_or_inning_half_is_never_invented():
    no_time = result()
    no_time.pop('wallclock')
    no_half = result('102')
    no_half['period'].pop('type')
    change = result('103', kind='lineup-change', text='Pitcher relieved starter.')
    pitch = result('104', kind='strike-swinging', text='Pitch 1: Strike 1 Swinging')
    pitch.update(type={'type': 'strike-swinging'}, summaryType='P')
    assert parse_plays({'plays': [no_time, no_half, change, pitch]}, 'mlb') == []


def test_mlb_scoreboard_retains_inning_and_count():
    event = {'id': '123', 'date': '2026-09-29T20:00Z',
             'status': {'period': 9, 'type': {'state': 'in', 'shortDetail': 'Top 9th'}},
             'competitions': [{'situation': {'balls': 2, 'strikes': 1, 'outs': 2},
                               'competitors': [{'homeAway': 'home', 'team': {'abbreviation': 'HOU'}, 'score': '3'},
                                               {'homeAway': 'away', 'team': {'abbreviation': 'CWS'}, 'score': '2'}]}]}
    game, = parse_games(League.MLB, {'events': [event]})
    assert (game.inning, game.inning_half, game.balls, game.strikes, game.outs) == (9, 'Top', 2, 1, 2)
