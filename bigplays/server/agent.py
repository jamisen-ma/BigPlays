"""Status/control for the separately supervised persistent highlight agent."""
from __future__ import annotations

import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from bigplays.config import settings
from bigplays.media.timeline import atomic_json, timestamp
from bigplays.orchestrator.live_agent import load_json
from bigplays.server.streams import failure

router = APIRouter()


@router.get('/api/agent')
def status():
    data = load_json(settings.agent_dir / 'status.json', {})
    try:
        running = time.time() - timestamp(data['heartbeat']) < 30
    except (KeyError, ValueError):
        running = False
    return {'ok': True, **data, 'running': running}


class Control(BaseModel):
    enabled: bool


@router.post('/api/agent/control')
def control(body: Control):
    atomic_json(settings.agent_dir / 'control.json', {'enabled': body.enabled})
    return {'ok': True, 'enabled': body.enabled}


class PlayCut(BaseModel):
    game_id: str = Field(pattern=r'^\d{1,20}$')
    play_id: str = Field(pattern=r'^\d{1,30}$')


@router.post('/api/agent/clip')
def clip(body: PlayCut):
    current = status()
    if not current['running'] or not current.get('enabled'):
        return failure('agent', 'Start or resume the background monitor first', 409)
    game = next((g for g in current.get('games', []) if g['game']['game_id'] == body.game_id), None)
    if not game:
        return failure('clip', 'This game is not currently being archived', 404)
    play = next((p for p in game['plays'] if p['play_id'] == body.play_id), None)
    if not play:
        return failure('clip', 'Play is not in the current timestamped play feed', 404)
    if play['status'] in ('outside recorded history', 'missing recorded segments'):
        return failure('clip', 'That play is not fully present in the retained recording; no replacement footage will be cut', 422)
    atomic_json(settings.agent_dir / 'requests' / f'{body.game_id}-{body.play_id}.json', body.model_dump())
    return {'ok': True, 'message': 'Play queued. Waiting for its matching video timestamp and complete window.'}
