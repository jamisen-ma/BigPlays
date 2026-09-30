"""Run real local inference with synthetic reactions, without social API access.

From the project root: .venv-local/bin/python -m scripts.validate_local_llm
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import httpx

from bigplays.config import settings
from bigplays.orchestrator.social_ranker import SocialJudge, apply_judgment


async def main():
    if settings.social_llm_provider != 'ollama':
        raise SystemExit('This smoke test requires SOCIAL_LLM_PROVIDER=ollama; it never calls a paid model.')
    game = {'name': 'Synthetic fixture: Houston at Georgia Southern'}
    play = {'play_id': 'fixture', 'occurred': time.time() - 120, 'period': 4, 'clock': '0:12',
            'text': 'Hughes breaks three tackles on a 65-yard game-winning touchdown run. No flags.'}
    cases = [
        ('play_excitement', ['Hughes breaking THREE tackles on that 65 yard winner is insane',
                            'That Hughes touchdown with 12 seconds left deserves every replay',
                            'How did Hughes stay upright on that winning run? Incredible balance.']),
        ('officiating', ['The refs missed holding on the Hughes run. Awful officiating.',
                        'Officials decided this game, that holding needed a flag.',
                        'Hughes touchdown only happened because the refs ignored the hold.']),
    ]
    original_dir = settings.agent_dir
    try:
        with tempfile.TemporaryDirectory(prefix='bigplays-local-smoke-') as directory:
            settings.agent_dir = Path(directory)
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                judge = SocialJudge(client)
                for name, bodies in cases:
                    comments = [{'id': str(i), 'body': body, 'created': play['occurred'] + 30,
                                 'url': 'https://example.invalid/synthetic/' + str(i)}
                                for i, body in enumerate(bodies)]
                    start = time.monotonic()
                    result = await judge.judge(game, play, [], comments, {'distinct_commenters': 3})
                    metadata = apply_judgment({'base_score': .6}, result, comments,
                                              {'distinct_commenters': 3}, 'fixture', 1)
                    if name == 'play_excitement':
                        assert metadata['social_assessment']['fan_backed'], result
                    else:
                        assert result.reaction == 'officiating', result
                        assert metadata['combined_score'] == .6
                    print(json.dumps({'synthetic_case': name, 'model': settings.social_llm_model,
                        'elapsed_seconds': round(time.monotonic() - start, 2),
                        'assessment': result.model_dump(), 'passed': True}), flush=True)
    finally:
        settings.agent_dir = original_dir


if __name__ == '__main__':
    asyncio.run(main())
