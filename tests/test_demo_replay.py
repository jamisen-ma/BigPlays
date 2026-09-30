import asyncio
import json
from datetime import datetime

from bigplays.demo.simulator import DemoSimulator
from bigplays.server.events import EventBus


def test_nfl_replay_preserves_source_time_and_streams_arrival(tmp_path):
    async def run():
        bus = EventBus()
        sim = DemoSimulator(bus, tmp_path / 'clips', tmp_path / 'fallback', league='nfl')
        assert len(sim.plays) == 5
        assert all(g['league'] == 'nfl' for g in sim.game_list())
        play = sim.plays[0]
        stages = []

        async def instant_stage(play, stage, detail, seconds, data=None):
            stages.append(stage)

        sim._stage = instant_stage
        record = await sim._run_play(play)
        assert record['demo'] is True
        assert record['occurred_utc'] == play.occurred_utc
        assert datetime.fromisoformat(record['received_utc']) > datetime.fromisoformat(record['occurred_utc'])
        assert record['source']['play_by_play_url'] == play.play_by_play_url
        assert record['file'] is None
        assert record['storage_uri'] is None
        assert stages == ['ingest', 'retrieve', 'heuristic', 'social', 'llm', 'clip', 'store']
        assert bus.recent(['highlight'])[0].data == record
        assert json.loads(next((tmp_path / 'clips').glob('*.json')).read_text()) == record
        game = sim.games[play.game_id]
        assert (game['away_score'], game['home_score']) == (play.away_after, play.home_after)

    asyncio.run(run())


def test_replay_copies_fallback_and_keeps_saved_clips_on_restart(tmp_path):
    async def run():
        sim = DemoSimulator(EventBus(), tmp_path / 'clips', tmp_path / 'fallback', league='nfl')
        sim.demo_clips_dir.mkdir()
        play = sim.plays[0]
        (sim.demo_clips_dir / f'{play.play_id}.mp4').write_bytes(b'fallback')

        async def instant_stage(*args, **kwargs):
            pass

        sim._stage = instant_stage
        record = await sim._run_play(play)
        assert (sim.clips_dir / record['file']).read_bytes() == b'fallback'
        (sim.clips_dir / 'real.json').write_text('{"demo": false}')
        restored = DemoSimulator(EventBus(), sim.clips_dir, sim.demo_clips_dir, league='nfl')
        assert restored.catalog.all() == [record]
        assert (sim.clips_dir / 'real.json').exists()
        assert (sim.clips_dir / record['file']).read_bytes() == b'fallback'

    asyncio.run(run())


def test_replay_arrivals_sort_ahead_of_older_captures(monkeypatch, tmp_path):
    from bigplays.server.app import load_highlights, settings

    monkeypatch.setattr(settings, 'clips_dir', tmp_path)
    monkeypatch.setattr(settings, 'demo_dataset', 'highlights')
    (tmp_path / 'replay.json').write_text(json.dumps({
        'event_id': 'replay', 'occurred_utc': '2025-12-21T04:29:15Z',
        'received_utc': '2026-09-29T12:00:00Z', 'file': None, 'demo': True,
    }))
    (tmp_path / 'live.json').write_text(json.dumps({
        'event_id': 'live', 'occurred_utc': '2026-09-28T12:00:00Z',
    }))
    assert [h['event_id'] for h in load_highlights()] == ['replay', 'live']


def test_week3_matches_source_timestamps_and_media_windows():
    from pathlib import Path
    from zoneinfo import ZoneInfo
    from bigplays.demo.replay import replay_plays

    evidence = json.loads((Path(__file__).parents[1] / 'bigplays/demo/data/nfl-2026-week3-evidence.json').read_text())
    originals = {row['play']['id']: row for row in evidence}
    plays = replay_plays('nfl-2026-week3', 'nfl')
    assert len(plays) == 15
    assert len({p.source_play_id for p in plays}) == 15
    assert len({p.game_id for p in plays}) == 6
    for play in plays:
        row = originals[play.source_play_id]
        source = row['play']
        assert (play.season, row['season_type'], play.week) == (2026, 2, 3)
        assert play.occurred_utc == source['wallclock']
        assert (play.period, play.clock) == (f'Q{source["period"]["number"]}', source['clock']['displayValue'])
        assert (play.away_after, play.home_after) == (source['awayScore'], source['homeScore'])
        assert (play.away_before, play.home_before) == (row['previous_scores']['away'], row['previous_scores']['home'])
        assert (play.video_start, play.video_end) == tuple(row['verified_video_window'])
        assert 0 <= play.video_start < play.video_end <= row['source_duration']
        event = datetime.fromisoformat(play.occurred_utc)
        assert event.tzinfo is not None
        assert play.date == datetime.fromisoformat(row['game_date']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        assert play.media_kind == 'broadcast'
    # Monday night in the US is Tuesday in UTC. Do not relabel the game date.
    monday = next(p for p in plays if p.game_id == '401872963')
    assert monday.date == '2026-09-28'
    assert monday.occurred_utc.startswith('2026-09-29')


def test_week3_clock_stays_at_source_time_and_media_is_reused(tmp_path):
    async def run():
        sim = DemoSimulator(EventBus(), tmp_path / 'clips', tmp_path / 'source',
                            league='nfl', dataset='nfl-2026-week3')
        play = sim.plays[0]
        assert sim.games[play.game_id]['clock'] == play.clock
        before = sim.game_list()
        task = asyncio.create_task(sim._tick_loop())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert sim.game_list() == before

        async def instant_stage(*args, **kwargs):
            pass

        sim._stage = instant_stage
        first = await sim._run_play(play)
        second = await sim._run_play(play)
        assert first['event_id'] == second['event_id']
        assert first['received_utc'] != second['received_utc']
        assert first['occurred_utc'] == second['occurred_utc'] == play.occurred_utc
        assert second['media_kind'] == 'broadcast'
        assert len(list(sim.clips_dir.glob('*.json'))) == 1
        assert second['clip_duration'] == play.video_end - play.video_start

    asyncio.run(run())


def test_week3_feed_excludes_other_games_without_removing_them(monkeypatch, tmp_path):
    from bigplays.server.app import load_highlights, settings
    monkeypatch.setattr(settings, 'clips_dir', tmp_path)
    monkeypatch.setattr(settings, 'demo_mode', True)
    monkeypatch.setattr(settings, 'demo_dataset', 'nfl-2026-week3')
    (tmp_path / 'live.json').write_text('{"event_id": "real"}')
    (tmp_path / 'week3.json').write_text(json.dumps({
        'event_id': 'week3', 'file': None, 'replay_dataset': 'nfl-2026-week3',
    }))
    assert [h['event_id'] for h in load_highlights()] == ['week3']
    assert (tmp_path / 'live.json').exists()


def test_imported_archive_restarts_without_sidecars_and_reuses_saved_media(tmp_path):
    from bigplays.storage.catalog import catalog_for

    async def run():
        clips = tmp_path / 'clips'
        clips.mkdir()
        record = {
            'event_id': 'nfl-official-source-id', 'imported': True,
            'replay_dataset': 'nfl-2026-week3', 'league': 'nfl',
            'game_id': '401872963', 'away': 'PHI', 'home': 'CHI',
            'away_color': '#004C54', 'home_color': '#0B162A',
            'away_score': 14, 'home_score': 10, 'season': 2026, 'week': 3,
            'title': 'Saved official highlight', 'description': 'Source play description',
            'period': 'Q3', 'clock': '7:32', 'file': 'saved.mp4',
            'occurred_utc': '2026-09-29T02:15:31Z',
            'received_utc': '2026-09-29T03:00:00Z',
        }
        video = clips / record['file']
        video.write_bytes(b'official source video')
        catalog_for(clips).upsert(record)
        bus = EventBus()
        sim = DemoSimulator(bus, clips, tmp_path / 'sources', league='nfl', dataset='nfl-2026-week3')
        assert sim.archived_plays == [record]
        assert len(sim.game_list()) == 1
        first = await sim._run_archived(record)
        second = await sim._run_archived(record)
        assert first['received_utc'] != second['received_utc']
        assert first['occurred_utc'] == second['occurred_utc'] == record['occurred_utc']
        assert first['file'] == second['file'] == 'saved.mp4'
        assert bus.recent(['highlight'])[0].data['occurred_utc'] == record['occurred_utc']
        restored = DemoSimulator(EventBus(), clips, tmp_path / 'sources', league='nfl', dataset='nfl-2026-week3')
        assert restored.archived_plays == [second]
        assert restored.catalog.all() == [second]
        assert video.read_bytes() == b'official source video'
        assert list(clips.glob('*.json')) == []

    asyncio.run(run())
