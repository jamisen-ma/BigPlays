"""Check discovery and a private resolver without logging its secret or signed URLs.

Run with the local services configured: python scripts/validate_live_stream.py
Optional --url selects an authorized event for the resolution check.
"""
import argparse
import asyncio
import json
import sys
import time
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from bigplays.config import settings
from bigplays.ingest.live_streams import discover


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='https://ppv.st/live/nfl-network')
    parser.add_argument('--record', action='store_true', help='Record and cut a real highlight through the FastAPI gateway')
    args = parser.parse_args()
    report = await discover()
    print(json.dumps({'discovery': {'ok': report['ok'], 'live': len(report['games']),
        'upcoming': {league: sum(g['league'] == league for g in report['upcoming']) for league in ('nba', 'nfl', 'ncaaf')},
        'matched_upcoming': sum(g.get('status') == 'matched' for g in report['upcoming']),
        'warnings': report['warnings']} }), flush=True)
    async with httpx.AsyncClient(timeout=55, trust_env=False) as client:
        response = await client.post(settings.resolver_base_url + '/api/stream',
            headers={'Authorization': 'Bearer ' + settings.resolver_api_key}, json={'url': args.url})
        data = response.json()
        if data.get('ok'):
            from urllib.parse import urlsplit
            print(json.dumps({'resolution': 'ok', 'media_host': urlsplit(data['streamUrl']).hostname}))
            # Persist privately for follow-up checks; do not print signed credentials.
            out = Path('data/validation/resolve.json')
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data))
            out.chmod(0o600)
            target = settings.resolver_base_url + data['proxiedUrl']
            for _ in range(4):
                relay = await client.get(target)
                if relay.status_code != 200:
                    print(json.dumps({'relay_status': relay.status_code, 'error': relay.text[:250]}), flush=True)
                    raise RuntimeError('Relay validation failed')
                if not relay.content.startswith(b'#EXTM3U'):
                    Path('data/validation/segment.bin').write_bytes(relay.content)
                    print(json.dumps({'relay': 'segment received', 'bytes': len(relay.content),
                                      'content_type': relay.headers.get('content-type')}), flush=True)
                    break
                lines = [line.strip() for line in relay.text.splitlines() if line.strip() and not line.startswith('#')]
                assert lines and all(line.startswith('/api/hls?') for line in lines)
                print(json.dumps({'relay': 'playlist', 'entries': len(lines)}), flush=True)
                target = settings.resolver_base_url + lines[0]
            if args.record:
                # TestClient exercises the real backend gateway and FFmpeg lifecycle.
                await asyncio.to_thread(record_and_clip, args.url)
        else:
            print(json.dumps({'resolution': data}))
            raise RuntimeError('Stream resolution failed')


def record_and_clip(url):
    # Keep validation segments separate from an active dashboard recording.
    settings.buffer_dir = Path('data/validation/buffer')
    from fastapi.testclient import TestClient
    from bigplays.server.app import app
    from bigplays.media.ffmpeg_utils import ffmpeg_bin
    with TestClient(app) as client:
        result = client.post('/api/stream', json={'url': url, 'record': True}).json()
        if not result.get('ok'):
            print(json.dumps({'record': result}), flush=True)
            raise RuntimeError('Recording could not start')
        print('Recording started; waiting for completed segments.', flush=True)
        preflight = client.get(result['proxiedUrl'])
        if preflight.status_code != 200:
            print(json.dumps({'record_relay': preflight.status_code, 'error': preflight.text[:250]}), flush=True)
        try:
            for _ in range(45):
                time.sleep(2)
                status = client.get('/api/recording').json()
                if not status['recording']:
                    print(json.dumps({'record': status}), flush=True)
                    raise RuntimeError('FFmpeg exited before a highlight was cut')
                clip = client.post('/api/recording/clip', json={'seconds': 14, 'title': 'Live stream validation'}).json()
                if clip.get('ok'):
                    path = settings.clips_dir / Path(clip['file']).name
                    check = subprocess.run([ffmpeg_bin(), '-v', 'error', '-i', str(path), '-f', 'null', '-'],
                                           capture_output=True, timeout=30)
                    assert check.returncode == 0, 'Highlight decoding failed'
                    response = client.get(clip['file'])
                    assert response.status_code == 200 and len(response.content) > 0
                    print(json.dumps({'record_cut_playback': 'passed', 'clip': str(path), 'bytes': len(response.content)}), flush=True)
                    return
            raise RuntimeError('No complete highlight after 90 seconds')
        finally:
            client.post('/api/recording/stop')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (RuntimeError, httpx.HTTPError, AssertionError) as error:
        print(f'Validation failed: {error}', file=sys.stderr)
        sys.exit(1)
