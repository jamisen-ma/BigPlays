"""Cache local scoreboard OCR for independent archive timestamp review."""
import concurrent.futures
import json
import subprocess
from pathlib import Path
from bigplays.media.ffmpeg_utils import ffmpeg_bin

ROOT = Path('data/nfl-week3/ocr')
ROOT.mkdir(exist_ok=True)

def review(video):
    target = ROOT / (video.stem + '.json')
    if target.exists():
        return
    frames = []
    for offset in (0.5, 2, 5):
        frame = ROOT / (video.stem + f'-{offset}.jpg')
        subprocess.run([ffmpeg_bin(), '-v', 'error', '-y', '-ss', str(offset), '-i', str(video), '-frames:v', '1', str(frame)], capture_output=True, check=True)
        if not frame.exists():
            continue
        result = subprocess.run(['data/tools/scoreboard-ocr', str(frame)], capture_output=True, check=True)
        rows = json.loads(result.stdout)
        frames.append({'offset': offset, 'rows': [r for r in rows if r['y'] < 0.3]})
        frame.unlink()
    target.write_text(json.dumps(frames, indent=2))

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for _ in pool.map(review, sorted(Path('data/clips').glob('nfl_*.mp4'))):
            pass
    print('OCR files:', len(list(ROOT.glob('*.json'))))
