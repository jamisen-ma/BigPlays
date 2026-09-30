"""MLB pitch-signature alignment: ESPN parsing, scorebug OCR, window selection, real footage."""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from bigplays.ingest import plays as play_feed
from bigplays.ingest.plays import (explain_mlb_alignment, locate_mlb_play, locate_play, mlb_pitches, mlb_roster,
                                   parse_plays, read_scorebug, team_aliases)
from bigplays.media.scoreboard import read_mlb_scorebug
from bigplays.media.timeline import TimelineArchive, align_mlb_play, select_window

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'data' / 'validation' / 'mlb' / 'live' / '401907924'
needs_live = pytest.mark.skipif(not (LIVE / 'espn-summary.json').exists() or not (LIVE / 'raw-ocr.json').exists(),
                                reason='saved BOS@NYY footage/OCR not present')

PITCHER, BATTER, NEXT = ('10', 'Cam Schlittler'), ('20', 'Wilyer Abreu'), ('21', 'Roman Anthony')


def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def pitch(pid, at_bat, number, before, after, kind='ball', batter=BATTER, pitcher=PITCHER, when='00:00:00',
          outs=0, home=0, away=0, pitch_count=None):
    return {'id': pid, 'atBatId': at_bat, 'atBatPitchNumber': number, 'summaryType': 'P',
            'type': {'type': kind}, 'text': f'Pitch {number}', 'wallclock': f'2026-09-29T20:{when[3:]}Z',
            'period': {'number': 1, 'type': 'Top'}, 'outs': outs, 'homeScore': home, 'awayScore': away,
            'pitchCount': pitch_count or {'balls': before[0], 'strikes': before[1]},
            'resultCount': {'balls': after[0], 'strikes': after[1]},
            'participants': [{'type': 'pitcher', 'athlete': {'id': pitcher[0]}},
                             {'type': 'batter', 'athlete': {'id': batter[0]}}]}


def payload(box_total=4):
    """Two plate appearances: a 2-pitch groundout, then a 2-pitch home run."""
    items = [
        {'id': '1', 'type': {'type': 'start-inning'}, 'period': {'number': 1, 'type': 'Top'}, 'outs': 0},
        {'id': '2', 'type': {'type': 'start-batterpitcher'}, 'summaryType': 'A', 'text': 'Cam Schlittler pitches to Roman Anthony',
         'participants': [{'type': 'pitcher', 'athlete': {'id': '10'}}, {'type': 'batter', 'athlete': {'id': '21'}}],
         'period': {'number': 1, 'type': 'Top'}, 'outs': 1},
        pitch('3', 'ab1', 1, (0, 0), (1, 0), batter=NEXT, outs=1),
        pitch('4', 'ab1', 2, (1, 0), (1, 0), kind='ground-out', batter=NEXT, outs=1),
        {'id': '5', 'type': {'type': 'play-result'}, 'alternativePlay': '4', 'alternativeType': {'type': 'ground-out'},
         'text': 'Anthony grounded out to short.', 'summaryType': 'N', 'wallclock': '2026-09-29T20:01:00Z',
         'period': {'number': 1, 'type': 'Top'}, 'outs': 1, 'homeScore': 0, 'awayScore': 0},
        {'id': '6', 'type': {'type': 'end-batterpitcher'}, 'period': {'number': 1, 'type': 'Top'}, 'outs': 1},
        # Replay review leaves ESPN's own pre-pitch count at an impossible 0-3.
        pitch('7', 'ab2', 1, (0, 0), (0, 1), kind='strike-looking', outs=1),
        pitch('8', 'ab2', 2, (0, 3), (0, 1), kind='home-run', outs=1, away=1, pitch_count={'balls': 0, 'strikes': 3}),
        {'id': '9', 'type': {'type': 'play-result'}, 'alternativePlay': '8', 'alternativeType': {'type': 'home-run'},
         'text': 'Abreu homered to right.', 'summaryType': 'N', 'wallclock': '2026-09-29T20:02:00Z', 'scoringPlay': True,
         'period': {'number': 1, 'type': 'Top'}, 'outs': 1, 'homeScore': 0, 'awayScore': 1,
         'pitchCount': {'balls': 0, 'strikes': 3}},
    ]
    return {'plays': items,
            'rosters': [{'team': {'id': '2'}, 'roster': [
                {'athlete': {'id': '20', 'displayName': 'Wilyer Abreu', 'lastName': 'Abreu'}},
                {'athlete': {'id': '21', 'displayName': 'Roman Anthony', 'lastName': 'Anthony'}}]}],
            'boxscore': {'players': [{'team': {'id': '10'}, 'statistics': [
                {'type': 'pitching', 'keys': ['IP', 'PC'], 'athletes': [
                    {'athlete': {'id': '10', 'displayName': 'Cam Schlittler', 'lastName': 'Schlittler'},
                     'stats': ['1.0', str(box_total)]}]}]}]}}


def test_mlb_plays_carry_roster_identities_pitch_number_and_pre_pitch_state():
    homer = {p['play_id']: p for p in parse_plays(payload(), 'mlb')}['9']
    assert (homer['batter'], homer['pitcher']) == ('Wilyer Abreu', 'Cam Schlittler')
    assert (homer['batter_id'], homer['pitcher_id']) == ('20', '10')
    assert homer['pitcher_pitch_count'] == 4          # cumulative across both batters, includes this pitch
    assert (homer['balls_before'], homer['strikes_before']) == (0, 1)   # from the prior pitch, not ESPN's 0-3
    assert homer['outs_before'] == 1 and (homer['home_score_before'], homer['away_score_before']) == (0, 0)
    assert homer['pitch_count_consistent'] and homer['pitch_play_id'] == '8'
    # A reliever missing from the box score is still named from "X pitches to Y".
    assert {p['name'] for p in mlb_roster(payload())} >= {'Cam Schlittler', 'Roman Anthony'}


def test_pitch_sequence_that_disagrees_with_box_score_is_never_aligned():
    homer = {p['play_id']: p for p in parse_plays(payload(box_total=3), 'mlb')}['9']   # a duplicated pitch
    assert homer['pitch_count_consistent'] is False
    assert align_mlb_play(homer, [])['reason'].startswith('ESPN pitch sequence disagrees')
    assert [p['pitcher_pitch_count'] for p in mlb_pitches(payload())] == [1, 2, 3, 4]


def row(text, x, y=.847, width=.05, confidence=1.):
    return {'text': text, 'x': x, 'y': y, 'width': width, 'height': .016, 'confidence': confidence}


ROSTER = [{'id': '10', 'name': 'Cam Schlittler', 'last': 'Schlittler', 'team': 'NYY'},
          {'id': '20', 'name': 'Wilyer Abreu', 'last': 'Abreu', 'team': 'BOS'},
          {'id': '30', 'name': 'Payton Tolle', 'last': 'Tolle', 'team': 'BOS'},
          {'id': '40', 'name': 'Ben Rice', 'last': 'Rice', 'team': 'NYY'}]

# Real Vision OCR rows from NBC's bug (top half: batter left; bottom half: pitcher left).
TOP_HALF = [row('AL WILD CARD - GAME1 (BEST OF 3)', .052, .938, .24), row('4. ABREU', .023),
            row('.773 OPS', .087), row('ISCHLITTLER', .118, width=.07), row('P: 19', .193, width=.03),
            row(':07', .291, .891), row('0-1', .286, .853, .03)]
BOTTOM_HALF = [row('TOLLE', .022, width=.04), row('P: 6 2. RICE', .099, width=.08), row('897 OPS', .187),
               row('0-1', .291, .853, .03)]


def test_nbc_scorebug_reads_both_layouts_with_fuzzy_last_names():
    top = read_scorebug(TOP_HALF, ROSTER, league='mlb')
    assert (top['batter_id'], top['pitcher_id'], top['pitch_count'], top['balls'], top['strikes']) == ('20', '10', 19, 0, 1)
    bottom = read_mlb_scorebug(BOTTOM_HALF, ROSTER)
    assert (bottom['batter_id'], bottom['pitcher_id'], bottom['pitch_count'], bottom['balls'], bottom['strikes']) == ('40', '30', 6, 0, 1)
    assert bottom['inning'] is None and bottom['outs'] is None    # graphical on NBC: never invented
    # team_aliases() carries the players, so the capture agent's existing aliases work.
    aliases = team_aliases(payload())
    assert read_scorebug(TOP_HALF, aliases, league='mlb')['pitcher'] == 'Cam Schlittler'


def test_batter_game_line_is_not_mistaken_for_the_count():
    roster = ROSTER + [{'id': '50', 'name': 'Paul Goldschmidt', 'last': 'Goldschmidt', 'team': 'NYY'}]
    rows = [row('TOLLE', .022, width=.033), row('P: 40 1. GOLDSCHMIDT', .094, width=.093),
            row('0-1', .198, .848, .016), row('••', .253, .85, .032), row('o-0', .282, .853, .038)]
    state = read_mlb_scorebug(rows, roster)
    assert (state['batter_id'], state['pitch_count'], state['balls'], state['strikes']) == ('50', 40, 0, 0)
    # Count unreadable: the "0-1" game line must not stand in for it.
    assert read_mlb_scorebug(rows[:4], roster)['balls'] is None


def test_scorebug_rejects_ambiguous_names_and_split_screens():
    twins = ROSTER + [{'id': '11', 'name': 'Tim Schlittler', 'last': 'Schlittler', 'team': 'NYY'}]
    state = read_mlb_scorebug(TOP_HALF, twins)
    assert state['pitcher_id'] is None and state['ambiguous']
    split = TOP_HALF + [row('P: 44', .7, .3, .03), row('2-2', .8, .3, .03)]
    assert read_mlb_scorebug(split, ROSTER) is None
    assert read_mlb_scorebug([row('P: 19', .2)], ROSTER)['batter'] is None
    assert read_scorebug(TOP_HALF, [['BOS'], ['NYY']]) is None     # football reader ignores baseball bugs


PLAY = {'play_id': '9', 'occurred': 1000., 'text': 'Abreu homered to right.', 'event_type': 'home-run',
        'pitcher_id': '10', 'batter_id': '20', 'pitcher_pitch_count': 20, 'balls_before': 0, 'strikes_before': 1,
        'pitch_count_consistent': True, 'inning': 1}


def obs(t, count=19, balls=0, strikes=1, batter='20', pitcher='10'):
    return {'time': t, 'pitcher_id': pitcher, 'batter_id': batter, 'pitch_count': count,
            'balls': balls, 'strikes': strikes, 'ambiguous': False}


def test_window_brackets_pre_pitch_signature_and_transition():
    frames = [obs(1010), obs(1012), obs(1014), obs(1020, 20, None, None, batter=None), obs(1022, 20, 0, 0, batter='21')]
    window = locate_play(PLAY, frames, league='mlb')
    assert window == locate_mlb_play(PLAY, frames)
    assert window['time'] == 1020 and window['start'] == 1014 - 2.5 - 5 and window['end'] == 1020 + 30
    assert explain_mlb_alignment(PLAY, frames)['reason'] == 'aligned'


def graphic(t, batter='20'):
    return {'time': t, 'pitch_graphic': True, 'velocity': 99, 'batter_id': batter, 'pitcher_id': None,
            'pitch_count': None, 'balls': None, 'strikes': None, 'ambiguous': False}


def test_velocity_graphic_tightens_release_and_closes_inning_ending_pitch():
    frames = [obs(1010), obs(1014), graphic(1016), obs(1020, 20), obs(1022, 20)]
    window = align_mlb_play(PLAY, frames)['window']
    assert window['release_latest'] == 1016 and window['closed_by'] == 'transition'
    # Third out: the bug leaves; only a later, different state (next half-inning) closes it.
    ending = [obs(1010), obs(1014), graphic(1016), graphic(1018)]
    assert 'waiting' in align_mlb_play(PLAY, ending)['reason']
    window = locate_play(PLAY, ending + [obs(1200, 3, 0, 0, batter='40', pitcher='30')], league='mlb')
    assert window['closed_by'] == 'pitch graphic' and window['end'] == 1016 + 5 + 30
    # NBC can flash the stale pre-pitch bug again after the graphic before the break.
    stale = [obs(1010), obs(1014), graphic(1016), obs(1020), obs(1300, 3, 0, 0, batter='40', pitcher='30')]
    window = locate_play(PLAY, stale, league='mlb')
    assert window['release_latest'] == 1016 and window['start'] == 1016 - 2.5 - 5
    # The same pitcher's P:N returning next inning must not stretch the clip to it.
    later = locate_play(PLAY, ending + [obs(1600, 20, 0, 0, batter='40'), obs(1602, 20, 0, 0, batter='40')], league='mlb')
    assert later['end'] == 1016 + 5 + 30


@pytest.mark.parametrize('frames, reason', [
    ([], 'not on screen'),
    ([obs(1010), obs(1012)], 'waiting for transition'),
    ([obs(1010), obs(1020, 20)], 'second frame'),
    ([obs(1010), obs(1011, 19, 1, 0), obs(1020, 20), obs(1022, 20)], 'contradicts'),   # count went backwards
    ([obs(1010, batter='21'), obs(1012), obs(1020, 20), obs(1022, 20)], 'contradicts'),
    ([obs(1005, 20), obs(1010), obs(1020, 20), obs(1022, 20)], 'ambiguous'),
    ([obs(1010), obs(1015, 18), obs(1020), obs(1030, 20), obs(1032, 20)], 'backwards'),
    ([obs(1010), obs(1100, 20), obs(1102, 20)], 'hidden'),
    ([obs(10), obs(20, 20), obs(22, 20)], 'not on screen'),     # wrong game/day: outside broadcast delay range
])
def test_ambiguous_or_missing_evidence_never_guesses(frames, reason):
    outcome = align_mlb_play(PLAY, frames)
    assert outcome['window'] is None and reason in outcome['reason']
    assert locate_play(PLAY, frames, league='mlb') is None


def test_football_locate_play_is_unchanged_for_clockless_plays():
    assert locate_play(PLAY | {'clock_seconds': None, 'period': 1}, [{'period': 1, 'clock_seconds': 0, 'time': 1000}]) is None


def live_observations(directory=LIVE):
    summary = json.loads((directory / 'espn-summary.json').read_text())
    aliases = team_aliases(summary)
    frames = json.loads((directory / 'raw-ocr.json').read_text())
    return summary, [r | {'time': f['time']} for f in frames if (r := read_scorebug(f['rows'], aliases, league='mlb'))]


@pytest.mark.parametrize('directory', [LIVE, LIVE.parents[1] / 'live3' / '401907924'])
def test_real_nbc_footage_windows_contain_hand_labelled_pitches(directory):
    if not (directory / 'ground-truth.json').exists() or not (directory / 'raw-ocr.json').exists():
        pytest.skip('saved BOS@NYY footage/OCR not present')
    summary, observations = live_observations(directory)
    truths = json.loads((directory / 'ground-truth.json').read_text())
    pitches = {p['play_id']: p for p in mlb_pitches(summary)}
    for truth in truths:
        window = locate_play(pitches[truth['play_id']], observations, league='mlb')
        assert window, truth['note']
        release, over = utc(truth['release_utc']), utc(truth['play_over_utc'])
        assert window['start'] + 4 <= release <= window['end'], truth['note']     # a few seconds of pre-roll
        assert over <= window['end'] and window['end'] - window['start'] < 60, truth['note']


@needs_live
def test_real_double_is_cut_from_the_buffer_through_the_capture_seam(tmp_path):
    from bigplays.orchestrator.live_agent import mlb_alignment, normalize_window
    summary, _ = live_observations()
    read, locate = mlb_alignment()
    aliases = team_aliases(summary)
    frames = json.loads((LIVE / 'raw-ocr.json').read_text())
    observations = [r | {'time': f['time']} for f in frames if (r := read(f['rows'], aliases))]
    double = next(p for p in parse_plays(summary, 'mlb') if p['text'] == 'Rice doubled to center.')
    window = normalize_window(locate(double, observations), double)
    assert window['start'] <= utc('2026-09-30T00:30:10.4Z') and utc('2026-09-30T00:30:19.0Z') <= window['end']
    archive = TimelineArchive(LIVE, 'http://127.0.0.1:3000')
    assert select_window(archive.segments, window['start'], window['end'])
    out = tmp_path / 'double.mp4'
    asyncio.run(archive.cut(window['start'], window['end'], out, {'play': double['text']}))
    assert out.stat().st_size > 100_000


def test_live_home_run_count_reset_before_pitch_count_is_the_transition():
    """BOS@NYY 2026-09-30, Rice homered off Erik Miller (ESPN pitch 3, pre-pitch 1-1)."""
    play = {'play_id': '4019079240903990057', 'occurred': 1790733088., 'text': 'Rice homered to center (423 feet).',
            'event_type': 'home-run', 'pitcher_id': '4152950', 'batter_id': '5016968', 'pitcher_pitch_count': 3,
            'balls_before': 1, 'strikes_before': 1, 'pitch_count_consistent': True, 'inning': 5}

    def seen(t, count=None, balls=None, strikes=None, batter='5016968', pitcher='4152950', velocity=None):
        return {'time': t, 'batter_id': batter, 'pitcher_id': pitcher if count is not None else None,
                'pitch_count': count, 'balls': balls, 'strikes': strikes, 'ambiguous': False,
                **({'pitch_graphic': True, 'velocity': velocity} if velocity else {})}
    frames = ([seen(1790733076.31, velocity=84), seen(1790733078.31, velocity=84)]
              + [seen(1790733080.31 + 2 * i, 2, 1, 1) for i in range(8)]
              + [seen(1790733096.33, velocity=97),                     # HR pitch graphic
                 seen(1790733100.33, 2, 1, 1), seen(1790733101.72, 2, 1, 1),   # stale pre-pitch bug
                 seen(1790733103.72, 2, 0, 0),                          # count reset before P increments
                 seen(1790733107.69, 3, 0, 0, batter=None), seen(1790733109.25, 3, 0, 0, batter=None),
                 seen(1790733111.25, 3, 0, 0, batter=None)])
    outcome = explain_mlb_alignment(play, frames)
    window = outcome['window']
    assert window, outcome['reason']
    release = utc('2026-09-30T01:51:35Z')
    assert window['start'] + 4 <= release <= window['release_latest'] == 1790733096.33
    assert window['end'] == 1790733096.33 + 5 + 30                   # home-run post-roll
    # A reset showing a different batter, or a backward count inside the signature, still refuses.
    other = frames[:13] + [seen(1790733103.72, 2, 0, 0, batter='39642')] + frames[14:]
    assert 'contradicts' in explain_mlb_alignment(play, other)['reason']
    backward = frames[:5] + [seen(1790733083.0, 2, 0, 1)] + frames[5:]
    assert 'contradicts' in explain_mlb_alignment(play, backward)['reason']


def test_live_box_score_running_ahead_for_current_pitcher_is_not_a_missing_pitch():
    live = payload(box_total=5)          # box score one pitch ahead of play-by-play
    homer = {p['play_id']: p for p in parse_plays(live, 'mlb')}['9']
    assert homer['pitch_count_consistent']
    assert not {p['play_id']: p for p in parse_plays(payload(box_total=7), 'mlb')}['9']['pitch_count_consistent']


RUTSCHMAN = {'play_id': '4019079241001990057', 'occurred': utc('2026-09-30T02:02:20Z'), 'outs': 3,
             'text': 'Rutschman struck out swinging.', 'event_type': 'strikeout', 'pitcher_id': '5134581',
             'batter_id': '42178', 'pitcher_pitch_count': 107, 'balls_before': 2, 'strikes_before': 2,
             'pitch_count_consistent': True, 'inning': 6}


def rut(t, count=None, balls=2, strikes=2, batter='42178', pitcher='5134581', velocity=None):
    return {'time': utc(t), 'batter_id': batter, 'pitcher_id': pitcher if count is not None else None,
            'pitch_count': count, 'balls': balls if count is not None else None,
            'strikes': strikes if count is not None else None, 'ambiguous': False,
            **({'pitch_graphic': True, 'velocity': velocity} if velocity else {})}


RUT_FRAMES = [rut(f'2026-09-30T02:02:{s}Z', 106) for s in (16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38)] + [
    rut('2026-09-30T02:02:41Z', velocity=88)]      # then a break: P:107 is never shown


def test_inning_ending_pitch_closes_from_graphic_without_waiting_for_the_break():
    window = locate_play(RUTSCHMAN, RUT_FRAMES, league='mlb')      # as soon as the graphic is read
    graphic = utc('2026-09-30T02:02:41Z')
    assert window['closed_by'] == 'pitch graphic (half-inning over)'
    assert window['release_latest'] == graphic and window['start'] <= graphic - 1.2 - 4
    assert window['end'] == graphic + 5 + 6                           # strikeout post-roll
    # The next half-inning (Bellinger vs Miller, 02:05:10) no longer changes the cut.
    later = RUT_FRAMES + [rut('2026-09-30T02:05:10Z', 5, 0, 0, batter='5016969', pitcher='4152950')]
    assert locate_play(RUTSCHMAN, later, league='mlb')['end'] == window['end']


def test_mid_inning_pitch_closes_from_graphic_once_p_n_is_overdue():
    play = RUTSCHMAN | {'outs': 2}
    assert 'waiting' in align_mlb_play(play, RUT_FRAMES)['reason']
    stale = RUT_FRAMES + [rut('2026-09-30T02:02:45Z', 106, 0, 0)]       # count reset, still P:106 (4s later)
    assert 'waiting' in align_mlb_play(play, stale)['reason']
    overdue = stale + [rut('2026-09-30T02:02:50Z', 106, 0, 0)]          # 9s after the graphic
    window = align_mlb_play(play, overdue)['window']
    assert window['closed_by'] == 'pitch graphic (P:N overdue)'


@pytest.mark.parametrize('bad', [rut('2026-09-30T02:02:44Z', 106, 0, 0, batter='5016969'),   # other batter
                                 rut('2026-09-30T02:02:30Z', 106, 1, 2)])                     # count backwards
def test_graphic_close_still_refuses_contradictions(bad):
    frames = sorted(RUT_FRAMES + [bad], key=lambda o: o['time'])
    outcome = align_mlb_play(RUTSCHMAN, frames)
    assert outcome['window'] is None and 'contradicts' in outcome['reason']


FRANCE = {'play_id': '401907974-france', 'occurred': utc('2026-09-30T03:30:50Z'), 'outs': 1, 'event_type': 'single',
          'text': 'France singled to left, Machado to second, Campusano to third.', 'pitcher_id': 'assad',
          'batter_id': 'france', 'pitcher_pitch_count': 18, 'balls_before': 1, 'strikes_before': 0,
          'pitch_count_consistent': True, 'inning': 5,
          'recent_pitches': [{'n': 16, 'batter_id': 'machado', 'balls': 2, 'strikes': 2},
                             {'n': 17, 'batter_id': 'france', 'balls': 0, 'strikes': 0}]}


def sd(t, count=None, balls=None, strikes=None, batter='france', velocity=None):
    return {'time': utc(f'2026-09-30T{t}Z'), 'batter_id': batter, 'pitcher_id': 'assad' if count is not None else None,
            'pitch_count': count, 'balls': balls, 'strikes': strikes, 'ambiguous': False,
            **({'pitch_graphic': True, 'velocity': velocity} if velocity else {})}


# CHC@SD live readings: NBC's "P:" for Assad runs one ahead of ESPN's pitch number.
FRANCE_FRAMES = [sd('03:30:09', 17, 0, 0, batter=None), sd('03:30:38', 17, 0, 0), sd('03:30:40', 17, 0, 0),
                 sd('03:30:44', velocity=91), sd('03:30:49', 18, 1, 0), sd('03:30:51', 18, 1, 0),
                 sd('03:31:08', velocity=92), sd('03:31:12', 19, 0, 0, batter=None),
                 sd('03:31:49', 19, 0, 0, batter='merrill')]


def test_constant_broadcast_pitch_offset_is_learned_from_the_pitchers_earlier_pitch():
    outcome = explain_mlb_alignment(FRANCE, FRANCE_FRAMES)
    window = outcome['window']
    assert window, outcome['reason']
    assert window['pitch_offset'] == 1 and '+1' in window['signature']
    release = utc('2026-09-30T03:31:07Z')
    assert window['release_earliest'] <= release <= window['release_latest'] == utc('2026-09-30T03:31:08Z')
    assert window['start'] <= release - 4 and window['end'] >= release + 10     # single post-roll


def test_unconfirmed_or_inconsistent_pitch_offset_refuses():
    unlearned = FRANCE | {'recent_pitches': []}
    assert 'not confirmed' in explain_mlb_alignment(unlearned, FRANCE_FRAMES)['reason']
    # The pitcher's latest earlier pitch implies a different offset (+2): +1 now is not trusted.
    exact_before = FRANCE | {'recent_pitches': [{'n': 16, 'batter_id': 'france', 'balls': 0, 'strikes': 0}]}
    assert 'not confirmed' in explain_mlb_alignment(exact_before, FRANCE_FRAMES)['reason']


def test_play_without_pitch_signature_has_a_stable_terminal_reason():
    from bigplays.media.timeline import NO_PITCH_SIGNATURE
    passed_ball = {'play_id': 'pb', 'occurred': utc('2026-09-30T03:36:00Z'), 'pitcher_id': 'assad',
                   'batter_id': None, 'pitcher_pitch_count': None, 'balls_before': None, 'strikes_before': None,
                   'text': 'France scored on a passed ball by Kelly, Merrill to second.', 'pitch_count_consistent': False}
    assert explain_mlb_alignment(passed_ball, FRANCE_FRAMES) == {'window': None, 'reason': NO_PITCH_SIGNATURE}
    assert NO_PITCH_SIGNATURE == 'not alignable from video: no pitch signature'
