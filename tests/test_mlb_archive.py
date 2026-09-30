import copy
import json
from pathlib import Path

import pytest

from bigplays.ingest import mlb_archive as mlb
from bigplays.storage.catalog import catalog_for


@pytest.fixture
def source():
    game = {
        'gamePk': 849845, 'gameType': 'F', 'season': '2026', 'officialDate': '2026-09-29',
        'gameDate': '2026-09-29T18:08:00Z', 'seriesDescription': 'Wild Card',
        'status': {'abstractGameState': 'Final', 'detailedState': 'Final'},
        'teams': {'away': {'team': {'name': 'Philadelphia Phillies', 'abbreviation': 'PHI'}, 'score': 3},
                  'home': {'team': {'name': 'Atlanta Braves', 'abbreviation': 'ATL'}, 'score': 5}},
    }
    play = {
        'about': {'inning': 8, 'halfInning': 'bottom', 'startTime': '2026-09-29T20:30:00Z',
                  'endTime': '2026-09-29T20:32:04Z', 'isScoringPlay': True},
        'result': {'eventType': 'home_run', 'awayScore': 3, 'homeScore': 5},
        'matchup': {'batter': {'fullName': 'Austin Riley'}, 'pitcher': {'fullName': 'Jhoan Duran'}},
        'playEvents': [
            {'playId': 'earlier-pitch', 'startTime': '2026-09-29T20:31:00Z', 'isPitch': True,
             'count': {'balls': 2, 'strikes': 0, 'outs': 1}},
            {'playId': 'exact-play', 'startTime': '2026-09-29T20:32:00Z', 'endTime': '2026-09-29T20:32:04Z',
             'isPitch': True, 'count': {'balls': 2, 'strikes': 1, 'outs': 1}},
        ],
    }
    item = {
        'id': 'riley-home-run', 'slug': 'riley-home-run', 'title': "Austin Riley's go-ahead home run",
        'description': 'Austin Riley hits a three-run home run in the bottom of the 8th inning',
        'guid': 'exact-play', 'date': '2026-09-29T20:37:10Z',
        'keywordsAll': [{'type': 'taxonomy', 'value': 'in-game-highlight'},
                        {'type': 'player_id', 'value': '663586', 'displayName': 'Austin Riley'}],
        'playbacks': [{'name': 'mp4Avc', 'url': 'https://mlb-cuts-diamond.mlb.com/clip.mp4'}],
    }
    feed = {'liveData': {'plays': {'allPlays': [play]}}}
    return game, item, feed


def record(source):
    game, item, feed = source
    return mlb.build_record(item, game, feed, Path('clip.mp4'), Path('clip.jpg'), 29)


def test_direct_guid_uses_event_time_not_at_bat_or_publication(source):
    saved = record(source)
    assert saved['occurred_utc'] == '2026-09-29T20:32:00Z'
    assert saved['occurred_utc'] != saved['published_utc']
    assert saved['play_start_utc'] == '2026-09-29T20:30:00Z'
    assert saved['source_play_id'] == 'exact-play'
    assert saved['timestamp_match_method'] == 'MLB content GUID'
    assert (saved['period'], saved['clock'], saved['inning_half']) == ('Bottom 8', '', 'bottom')
    assert (saved['away_score'], saved['home_score']) == (3, 5)
    assert (saved['balls'], saved['strikes'], saved['outs'], saved['count_context']) == (2, 0, 1, 'before pitch')
    assert saved['demo'] is False and saved['league'] == 'mlb'


def test_unmatched_explicit_guid_never_falls_back_to_publication(source):
    source[1]['guid'] = 'unknown-guid'
    saved = record(source)
    assert saved['occurred_utc'] is None
    assert saved['timestamp_status'] == 'unresolved'
    assert saved['period'] == '' and saved['inning'] is None
    assert saved['published_utc'] == source[1]['date']


def test_unique_description_matches_but_ambiguous_home_runs_do_not(source):
    del source[1]['guid']
    assert record(source)['timestamp_match_method'] == 'MLB unique batter/event description'
    source[2]['liveData']['plays']['allPlays'].append(copy.deepcopy(source[2]['liveData']['plays']['allPlays'][0]))
    assert record(source)['occurred_utc'] is None


def test_reviewed_abs_mapping_identifies_original_pitch(source, monkeypatch, tmp_path):
    game, item, feed = source
    feed['gamePk'] = game['gamePk']
    item['id'] = 'reviewed-capture-review'
    item.pop('guid')
    monkeypatch.setattr(mlb, '__file__', str(tmp_path / 'mlb_archive.py'))
    (tmp_path / 'mlb_play_matches.json').write_text(json.dumps({
        '849845/reviewed-capture-review': {'play_id': 'exact-play', 'evidence': 'Reviewed footage'},
    }))
    saved = record(source)
    assert saved['timestamp_match_method'] == 'MLB original reviewed pitch'
    assert saved['occurred_utc'] == '2026-09-29T20:32:00Z'


def test_source_video_cannot_precede_play(source):
    source[1]['date'] = '2026-09-29T18:00:00Z'
    assert record(source)['occurred_utc'] is None


def test_first_pitch_count_uses_prior_outs_in_same_half_inning(source):
    game, item, feed = source
    play = feed['liveData']['plays']['allPlays'][0]
    play['playEvents'] = [play['playEvents'][-1]]
    previous = {'about': {'inning': 8, 'halfInning': 'bottom'}, 'count': {'outs': 2}}
    feed['liveData']['plays']['allPlays'].insert(0, previous)
    saved = record(source)
    assert (saved['balls'], saved['strikes'], saved['outs']) == (0, 0, 2)
    previous['about']['halfInning'] = 'top'
    assert record(source)['outs'] == 0


def test_individual_filter_excludes_pregame_and_compilations(source):
    item = source[1]
    assert mlb.is_individual(item)
    for title in ["Chris Sale strikes out nine in Game 1", "Starting lineup", "National Anthem", "Ceremonial first pitch"]:
        assert not mlb.is_individual({**item, 'title': title})
    assert not mlb.is_individual({**item, 'keywordsAll': [{'type': 'taxonomy', 'value': 'game-recap'}]})


def test_playback_uses_official_download_and_rejects_other_hosts(source):
    item = source[1]
    item['playbacks'].insert(0, {'name': 'hlsCloud', 'url': 'https://mlb-cuts-diamond.mlb.com/clip.m3u8'})
    assert mlb.playback_url(item).endswith('.mp4')
    item['playbacks'] = [{'name': 'mp4Avc', 'url': 'https://mlb.com.invalid.example/video.mp4'}]
    with pytest.raises(ValueError, match='official'):
        mlb.playback_url(item)


def test_game_status_and_start_time(source):
    game = source[0]
    normalized = mlb.normalize_game(game)
    assert (normalized['status'], normalized['away'], normalized['home']) == ('post', 'PHI', 'ATL')
    assert normalized['starts_at'] == game['gameDate']
    assert normalized['series_description'] == 'Wild Card'
    game['status'] = {'abstractGameState': 'Live', 'detailedState': 'In Progress'}
    feed = {'liveData': {'linescore': {'currentInning': 4, 'inningHalf': 'Top', 'balls': 1, 'strikes': 2, 'outs': 2}}}
    normalized = mlb.normalize_game(game, feed)
    assert (normalized['status'], normalized['period'], normalized['clock']) == ('in', 'Top 4', '')


def test_repeated_import_restores_video_and_does_not_duplicate_or_change_arrival(source, monkeypatch, tmp_path):
    game, item, feed = source
    monkeypatch.setattr(mlb, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(mlb.settings, 'clips_dir', tmp_path / 'clips')
    monkeypatch.setattr(mlb.settings, 'database_path', tmp_path / 'catalog.sqlite3')
    monkeypatch.setattr(mlb, 'fetch_schedule', lambda _: {'dates': [{'games': [game]}]})
    monkeypatch.setattr(mlb, 'fetch_game', lambda _: ({'highlights': {'highlights': {'items': [item, item]}}}, feed))
    downloads = []

    def download(item, game_pk, clips_dir):
        downloads.append(item['id'])
        target = clips_dir / (mlb.stable_id(game_pk, item) + '.mp4')
        target.write_bytes(b'persisted video')
        target.with_suffix('.jpg').write_bytes(b'poster')
        return target, target.with_suffix('.jpg'), 29

    monkeypatch.setattr(mlb, 'download', download)
    first = mlb.import_date('2026-09-29')
    catalog = catalog_for(mlb.settings.clips_dir, mlb.settings.database_path)
    saved = catalog.all()
    second = mlb.import_date('2026-09-29')
    assert first['downloaded'] == first['new'] == first['timestamp_matched'] == 1
    assert second['new'] == 0 and second['failures'] == []
    assert catalog.all() == saved
    assert downloads == [item['id']]
    assert catalog.imports()['mlb-2026-09-29']['game_count'] == 1
    (mlb.settings.clips_dir / saved[0]['file']).unlink()
    third = mlb.import_date('2026-09-29')
    assert third['downloaded'] == 1 and third['new'] == 0
    assert len(downloads) == 2
    assert catalog.all() == saved
