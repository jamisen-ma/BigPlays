import asyncio
import json
from types import SimpleNamespace

from bigplays.config import settings
from bigplays.orchestrator.live_agent import GameMonitor


def test_background_scoring_play_cuts_historical_window_once_without_manual_request(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'agent_dir', tmp_path / 'agent')
    monkeypatch.setattr(settings, 'clips_dir', tmp_path / 'clips')
    game = {'league': 'ncaaf', 'game_id': '123', 'name': 'Away at Home'}
    play = {'play_id': '1234', 'occurred': 1000, 'period': 2, 'clock': '8:28',
            'clock_seconds': 508, 'interesting': True, 'text': 'Touchdown', 'home_score': 7, 'away_score': 0}
    calls = []

    async def exercise():
        for _ in range(2):  # Simulate a restart with the same scored play in ESPN's feed.
            monitor = GameMonitor(game)
            monitor.social = SimpleNamespace(approved=lambda p: True,
                decision_for=lambda p: {'assessment': {'fan_backed': True}, 'combined_score': .9})
            monitor.plays = [play]
            monitor.observations = [{'time': 1060, 'period': 2, 'clock': '8:28', 'clock_seconds': 508}]
            monitor.archive.segments = [{'start': 1040, 'duration': 30, 'file': 'play.ts'},
                                        {'start': 2000, 'duration': 30, 'file': 'new-ad.ts'}]

            async def cut(start, end, out, metadata, *, still_approved):
                assert still_approved()
                calls.append((start, end, metadata))
                out.parent.mkdir(parents=True, exist_ok=True)
                out.with_suffix('.json').write_text(json.dumps(metadata))

            monitor.archive.cut = cut
            task = asyncio.create_task(monitor.clips())
            await asyncio.sleep(.01)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert len(calls) == 1
    assert calls[0][:2] == (1048, 1064)
    assert calls[0][2]['play_id'] == '1234'
    assert calls[0][2]['alignment']['offset_seconds'] == 60
