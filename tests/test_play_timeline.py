import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from bigplays.ingest.plays import locate_play, parse_plays, read_scorebug
from bigplays.media.ffmpeg_utils import ffmpeg_bin, media_duration_seconds
from bigplays.media.timeline import TimelineArchive, parse_playlist, relay_target, select_window, timestamp


def test_program_dates_follow_durations_not_download_times():
    result = parse_playlist('''#EXTM3U
#EXT-X-MEDIA-SEQUENCE:40
#EXT-X-PROGRAM-DATE-TIME:2026-09-26T18:00:00Z
#EXTINF:4,
/api/hls?sig=one
#EXTINF:5,
/api/hls?sig=two
''')
    assert result[1]['start'] == timestamp('2026-09-26T18:00:04Z')
    assert result[1]['sequence'] == 41
    with pytest.raises(ValueError, match='mapping'):
        parse_playlist('#EXTM3U\n#EXTINF:4,\n/api/hls?sig=a')
    with pytest.raises(ValueError, match='mapping'):
        parse_playlist('#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:2026-09-26T18:00:00Z\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/api/hls?sig=a')


def test_archive_cannot_fetch_arbitrary_urls():
    for value in ['https://evil.test/x', '//evil.test/api/hls?q=a', '/api/stream?q=a']:
        with pytest.raises(ValueError):
            relay_target('http://127.0.0.1:3000', value)


def test_old_play_window_does_not_fall_back_to_latest_ad():
    segments = [{'start': 100, 'duration': 4, 'file': 'play.ts'},
                {'start': 104, 'duration': 4, 'file': 'play-end.ts'},
                {'start': 200, 'duration': 4, 'file': 'ad.ts'}]
    assert [s['file'] for s in select_window(segments, 102, 106)] == ['play.ts', 'play-end.ts']
    for start, end in [(90, 102), (106, 202), (202, 210)]:
        with pytest.raises(ValueError):
            select_window(segments, start, end)


def test_sliding_playlist_rounding_does_not_duplicate_footage():
    segments = [{'start': 100.001, 'duration': 4, 'sequence': 10, 'file': 'one.ts'},
                {'start': 100.002, 'duration': 4, 'sequence': 10, 'file': 'duplicate.ts'},
                {'start': 104.001, 'duration': 4, 'sequence': 11, 'file': 'two.ts'}]
    assert [s['file'] for s in select_window(segments, 101, 107)] == ['one.ts', 'two.ts']


def test_plays_require_real_utc_and_detect_touchdown_without_score_value():
    play = {'id': '123', 'wallclock': '2026-09-26T18:00:00Z', 'period': {'number': 2},
            'clock': {'displayValue': '1:23'}, 'text': 'Touchdown!', 'scoringPlay': True,
            'type': {'text': 'Passing Touchdown'}}
    result = parse_plays({'drives': {'current': {'plays': [play]}}}, 'ncaaf')
    assert result[0]['occurred'] == timestamp(play['wallclock'])
    assert result[0]['interesting']
    del play['wallclock']
    assert parse_plays({'plays': [play]}, 'ncaaf') == []


def test_alignment_requires_the_correct_quarter_clock_and_teams():
    play = {'occurred': 1000, 'period': 2, 'clock_seconds': 83}
    assert locate_play(play, [{'time': 1050, 'period': 1, 'clock_seconds': 83}]) is None
    assert locate_play(play, [{'time': 1050, 'period': 2, 'clock_seconds': 200}]) is None
    matched = {'time': 1050, 'period': 2, 'clock_seconds': 83}
    assert locate_play(play, [matched, matched | {'time': 1080}]) == matched
    rows = [{'text': text, 'confidence': 1, 'x': x, 'y': .1} for text, x in
            [('OKLAHOMA', .2), ('1:23', .5), ('2ND', .5), ('GEORGIA', .7)]]
    assert read_scorebug(rows, [['OKLAHOMA'], ['GEORGIA']])['period'] == 2
    assert read_scorebug(rows, [['ILLINOIS'], ['OHIO STATE']]) is None
    rows[2]['text'] = '2ND & 5'
    assert read_scorebug(rows, [['OKLAHOMA'], ['GEORGIA']]) is None


def test_timestamp_cut_encodes_past_play_instead_of_newer_ad(tmp_path):
    archive = TimelineArchive(tmp_path / 'archive', 'http://127.0.0.1:3000')
    for name, color in [('play', 'red'), ('ad', 'blue')]:
        subprocess.run([ffmpeg_bin(), '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:s=160x90:r=25:d=4',
                        '-c:v', 'libx264', '-f', 'mpegts', str(archive.directory / f'{name}.ts')], check=True)
    archive.segments = [{'start': 100, 'duration': 4, 'file': 'play.ts'},
                        {'start': 200, 'duration': 4, 'file': 'ad.ts'}]
    out = tmp_path / 'clip.mp4'
    asyncio.run(archive.cut(101, 103, out, {'play_id': '123'}))
    assert abs(media_duration_seconds(out) - 2) < .2
    frame = tmp_path / 'frame.png'
    subprocess.run([ffmpeg_bin(), '-v', 'error', '-i', str(out), '-frames:v', '1', str(frame)], check=True)
    red, green, blue = Image.open(frame).convert('RGB').getpixel((80, 45))
    assert red > 200 and blue < 30
    assert json.loads(out.with_suffix('.json').read_text())['play_id'] == '123'
