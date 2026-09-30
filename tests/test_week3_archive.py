import json
from pathlib import Path

import pytest

from bigplays.ingest import week3_archive as archive


def source_item(title='Jelani Woods reaches the top shelf for a 12-yard grab'):
    return {
        'id': 'official-video', 'mcpID': '123', 'title': title,
        'category': 'NFL Game Highlights',
        'description': '<p>Jelani Woods makes a 12-yard catch.</p>',
        'source_url': 'https://www.nfl.com/videos/official-video',
        'tags': [
            {'title': 'Away Team at Home Team (2026-REG-3)', 'gameId': 'game'},
            {'title': 'Jelani Woods', 'personId': 'player'},
        ],
        'schema': [{'@type': 'NewsArticle', 'datePublished': '2026-09-29T04:00:00Z'}],
    }


def source_play(play_id='one', **extra):
    return {
        'id': play_id, 'text': 'J.Woods pass complete for 12 yards.',
        'type': {'text': 'Pass Reception'}, 'statYardage': 12,
        'period': {'number': 2}, 'clock': {'displayValue': '8:47'},
        'awayScore': 7, 'homeScore': 10,
        'wallclock': '2026-09-29T01:28:11Z', **extra,
    }


def source_event():
    return {
        'id': 'game', 'date': '2026-09-29T00:15:00Z',
        'competitions': [{'competitors': [
            {'homeAway': 'away', 'team': {'abbreviation': 'AWY', 'color': '123456'}},
            {'homeAway': 'home', 'team': {'abbreviation': 'HME', 'color': '654321'}},
        ]}],
    }


def test_individual_top_shelf_highlight_is_kept():
    assert archive.is_individual(source_item())


@pytest.mark.parametrize('title', [
    'Away vs. Home highlights | Week 3', 'Best plays from Week 3',
    'Away at Home preview', 'Top catches from Week 3',
])
def test_recaps_previews_and_compilations_are_excluded(title):
    assert not archive.is_individual(source_item(title))


def test_compilation_tag_and_wrong_week_are_excluded():
    item = source_item()
    item['tags'].append({'slug': 'player-highlights-vc'})
    assert not archive.is_individual(item)
    item = source_item()
    item['tags'][0]['title'] = 'Away Team at Home Team (2026-REG-2)'
    assert not archive.is_individual(item)


def test_matching_requires_unique_play_and_source_wallclock():
    play = source_play()
    match, candidates = archive.match_play(source_item(), [play])
    assert match is play
    assert candidates == ['one']
    match, candidates = archive.match_play(source_item(), [play, source_play('two')])
    assert match is None
    assert set(candidates) == {'one', 'two'}
    match, candidates = archive.match_play(source_item(), [source_play(wallclock=None)])
    assert match is None
    assert candidates == []


def test_quarter_and_yardage_disambiguate_repeated_player_plays():
    item = source_item('Jelani Woods makes a 12-yard catch in the second quarter')
    item['description'] = 'Jelani Woods makes a 12-yard catch in the second-quarter.'
    expected = source_play('correct')
    wrong_quarter = source_play('first-quarter', period={'number': 1})
    wrong_yards = source_play('different-catch', statYardage=25)
    match, candidates = archive.match_play(item, [wrong_quarter, wrong_yards, expected])
    assert match is expected
    assert candidates == ['correct']


def test_destination_yard_line_is_not_a_gain():
    item = source_item('Jelani Woods reaches the 12-yard line')
    item['description'] = '<p>Jelani Woods gets inside the 12-yard line.</p>'
    assert archive.match_play(item, [source_play()]) == (None, [])


def test_play_cannot_happen_after_video_publication():
    item = source_item()
    item['schema'][0]['datePublished'] = '2026-09-29T01:00:00Z'
    match, candidates = archive.match_play(item, [source_play()])
    assert match is None
    assert candidates == ['one']


@pytest.mark.parametrize('matched', [True, False])
def test_original_event_time_never_uses_publication_or_import_time(matched):
    item, play = source_item(), source_play()
    record = archive.build_record(item, source_event(), [play], play if matched else None,
                                  Path('video.mp4'), Path('video.jpg'), 18.5, [play['id']])
    assert record['published_utc'] == '2026-09-29T04:00:00Z'
    assert record['received_utc'] != record['published_utc']
    assert record['occurred_utc'] == (play['wallclock'] if matched else None)
    assert record['timestamp_status'] == ('matched' if matched else 'unresolved')
    assert record['source_play_id'] == (play['id'] if matched else None)
    assert record['clock'] == ('8:47' if matched else '')
    assert record['date'] == '2026-09-28'  # Monday night in the US, Tuesday in UTC.
    assert record['description'] == 'Jelani Woods makes a 12-yard catch.'
    assert record['clip_duration'] == record['video_end'] == 18.5


def test_reimport_keeps_one_durable_record_and_initial_import_time(monkeypatch, tmp_path):
    cache, clips = tmp_path / 'source', tmp_path / 'clips'
    cache.mkdir()
    item = source_item()
    (cache / 'nfl-single-plays.json').write_text(json.dumps([item, item]))
    monkeypatch.setattr(archive, 'CACHE', cache)
    monkeypatch.setattr(archive.settings, 'clips_dir', clips)
    monkeypatch.setattr(archive.settings, 'database_path', None)
    monkeypatch.setattr(archive, 'game_sources', lambda: {'Away Team at Home Team': (source_event(), [source_play()])})

    def saved_media(item, directory):
        video, poster = directory / 'saved.mp4', directory / 'saved.jpg'
        if not video.exists():
            video.write_bytes(b'previously downloaded video')
            poster.write_bytes(b'poster')
        return video, poster, 18.5

    monkeypatch.setattr(archive, 'download', saved_media)
    first_report = archive.run(workers=1)
    catalog = archive.catalog_for(clips)
    first = catalog.all()
    second_report = archive.run(workers=1)
    assert len(first) == len(catalog.all()) == 1
    assert catalog.all()[0]['received_utc'] == first[0]['received_utc']
    assert first_report['discovered'] == second_report['downloaded'] == 1
    assert second_report['failures'] == []
    assert catalog.imports()[archive.DATASET]['downloaded'] == 1
    assert (clips / 'saved.mp4').read_bytes() == b'previously downloaded video'
