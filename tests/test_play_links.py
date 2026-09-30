import json
import sqlite3

from bigplays.storage import play_links
from bigplays.storage.catalog import HighlightCatalog
from bigplays.storage.play_links import LinkIndex, attach, clip_ref, source_kind_of


def play(pid, **kw):
    return {'play_id': pid, 'sequence': int(pid[-2:]), 'text': f'play {pid}', **kw}


def clip(event_id, tmp_path=None, **kw):
    return clip_ref({'event_id': event_id, 'game_id': 'G1', 'league': 'nfl', **kw}, tmp_path)


def test_source_kind_derivation():
    assert source_kind_of({'demo': True, 'imported': True}) == 'replay'
    assert source_kind_of({'imported': True}) == 'official_upload'
    assert source_kind_of({}) == 'live_capture'
    assert source_kind_of({'demo': True, 'source_kind': 'live_capture'}) == 'live_capture'
    assert source_kind_of({'source_kind': 'bogus', 'imported': True}) == 'official_upload'


def test_exact_match_by_source_play_id_play_id_and_related(tmp_path):
    plays = [play('P01'), play('P02', related_play_ids=['P02a', 'P02b']), play('P03')]
    clips = [clip('a', source_play_id='P01'), clip('b', play_id='P03'), clip('c', play_id='P02b')]
    out, unmatched = attach(plays, clips)
    assert [p['clip']['event_id'] if p['clip'] else None for p in out] == ['a', 'c', 'b']
    assert all(p['viral'] and p['viral_reason'] for p in out)
    assert out[0]['clip']['match_method'] == 'exact'
    assert unmatched == []
    assert 'clip' not in plays[0]  # inputs untouched


def test_candidate_match_and_exact_wins_over_candidates(tmp_path):
    plays = [play('P01'), play('P02')]
    out, unmatched = attach(plays, [clip('a', source_play_id='X', play_candidates=['nope', 'P02']),
                                    clip('b', source_play_id='P01', play_candidates=['P02'])])
    assert out[1]['clip']['event_id'] == 'a' and out[1]['clip']['match_method'] == 'candidate'
    assert out[0]['clip']['event_id'] == 'b'
    assert unmatched == []


def test_unmatched_never_time_matched(tmp_path):
    plays = [play('P01', wallclock_utc='2026-09-25T00:39:57Z')]
    out, unmatched = attach(plays, [clip('a', source_play_id='X', occurred_utc='2026-09-25T00:39:57Z')])
    assert out[0]['clip'] is None and out[0]['viral'] is False and out[0]['viral_reason'] is None
    assert [c['event_id'] for c in unmatched] == ['a']
    assert not any(k.startswith('_') for k in unmatched[0])


def test_multi_clip_selection_prefers_live_then_social_then_latest(tmp_path):
    plays = [play('P01'), play('P02'), play('P03')]
    clips = [
        clip('replay_hi', source_play_id='P01', demo=True, social_score=0.99),
        clip('live', source_play_id='P01'),
        clip('official_lo', source_play_id='P02', imported=True, social_score=0.2),
        clip('official_hi', source_play_id='P02', imported=True, social_score=0.8),
        clip('old', source_play_id='P03', imported=True, received_utc='2026-09-01T00:00:00Z'),
        clip('new', source_play_id='P03', imported=True, received_utc='2026-09-02T00:00:00Z'),
    ]
    out, _ = attach(plays, clips)
    assert out[0]['clip']['event_id'] == 'live' and out[0]['clip']['source_kind'] == 'live_capture'
    assert [c['event_id'] for c in out[0]['alternate_clips']] == ['replay_hi']
    assert out[1]['clip']['event_id'] == 'official_hi'
    assert out[1]['viral_reason'] == 'Official highlight · fan buzz 0.80'
    assert out[2]['clip']['event_id'] == 'new'


def test_clip_ref_urls_match_highlights_scheme(tmp_path):
    (tmp_path / 'a.mp4').write_bytes(b'x')
    ref = clip('a', tmp_path, file='a.mp4', poster='a.jpg', clip_duration=12.5)
    assert ref['video_url'] == '/clips/a.mp4' and ref['poster_url'] == '/clips/a.jpg'
    assert ref['duration_seconds'] == 12.5
    missing = clip('b', tmp_path, file='b.mp4', youtube_id='yt1')
    assert missing['video_url'] is None
    assert missing['poster_url'] == 'https://i.ytimg.com/vi/yt1/mqdefault.jpg'


def test_mlb_game_state_match_must_be_unique(tmp_path):
    state = dict(league='mlb', inning=6, inning_half='bottom', away_score=1, home_score=2,
                 balls=1, strikes=1, outs=1, imported=True, source_play_id='mlb-guid')
    plays = [play('P01', period=6, period_label='Bottom 6', away_score=1, home_score=2,
                  mlb={'balls': 1, 'strikes': 1, 'outs': 1}),
             play('P02', period=6, period_label='Bottom 6', away_score=1, home_score=2,
                  mlb={'balls': 0, 'strikes': 0, 'outs': 1})]
    out, unmatched = attach(plays, [clip_ref({'event_id': 'm', 'game_id': '849845', **state})], 'mlb')
    assert out[0]['clip']['match_method'] == 'mlb_game_state' and unmatched == []
    twin = [plays[0], dict(plays[0], play_id='P09')]
    out, unmatched = attach(twin, [clip_ref({'event_id': 'm', 'game_id': '849845', **state})], 'mlb')
    assert [c['event_id'] for c in unmatched] == ['m']


def test_index_refreshes_on_catalog_change_and_maps_mlb_games(tmp_path):
    db = tmp_path / 'clips.sqlite3'
    catalog = HighlightCatalog(db)
    catalog.upsert({'event_id': 'n1', 'game_id': '401', 'league': 'nfl', 'source_play_id': '40101'})
    index = LinkIndex(db, tmp_path)
    assert index.refresh() == []  # first build reports nothing as "new"
    assert [c['event_id'] for c in index.clips_for_game('nfl', '401')] == ['n1']
    catalog.upsert({'event_id': 'n2', 'game_id': '401', 'league': 'nfl', 'play_id': '40102'})
    catalog.upsert({'event_id': 'm1', 'game_id': '849845', 'league': 'mlb', 'date': '2026-09-29',
                    'away': 'CWS', 'home': 'HOU'})
    assert index.refresh() == [('mlb', '849845'), ('nfl', '401')]
    assert index.refresh() == []
    assert len(index.clips_for_game('nfl', '401')) == 2
    espn_game = {'game_id': '401907896', 'start_utc': '2026-09-29T18:10:00Z',
                 'away': {'abbr': 'CHW'}, 'home': {'abbr': 'HOU'}}
    assert [c['event_id'] for c in index.clips_for_game('mlb', '401907896', espn_game)] == ['m1']
    assert index.clips_for_game('mlb', '401907896') == []


def test_index_is_read_only(tmp_path):
    db = tmp_path / 'clips.sqlite3'
    HighlightCatalog(db).upsert({'event_id': 'n1', 'game_id': '401', 'league': 'nfl'})
    LinkIndex(db, tmp_path).refresh()
    with sqlite3.connect(db) as conn:
        assert conn.execute('select count(*) from highlights').fetchone() == (1,)


def test_mlb_game_state_tie_broken_by_batter_name():
    state = dict(league='mlb', inning=5, inning_half='top', away_score=1, home_score=1, balls=2,
                 strikes=2, outs=2, imported=True, title='Sale escapes jam',
                 description='Chris Sale strikes out J.T. Realmuto')
    base = dict(period=5, period_label='Top 5', away_score=1, home_score=1)
    plays = [play('P01', **base, mlb={'balls': 2, 'strikes': 2, 'outs': 2, 'batter': 'Bryson Stott'}),
             play('P02', **base, mlb={'balls': 2, 'strikes': 2, 'outs': 3, 'batter': 'J.T. Realmuto'})]
    out, unmatched = attach(plays, [clip_ref({'event_id': 'k', 'game_id': '849845', **state})], 'mlb')
    assert out[1]['clip']['event_id'] == 'k' and out[0]['clip'] is None and unmatched == []
