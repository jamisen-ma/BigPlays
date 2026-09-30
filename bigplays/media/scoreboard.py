"""Local frame OCR; absent/unreadable clocks keep automatic cuts pending."""
from __future__ import annotations

import asyncio
import csv
import difflib
import io
import json
import re
import unicodedata
from pathlib import Path

from bigplays.media.ffmpeg_utils import ffmpeg_bin


def tesseract_rows(tsv: str) -> list[dict]:
    """Convert TSV lines to Vision's normalized, bottom-left coordinates."""
    records = list(csv.DictReader(io.StringIO(tsv), delimiter='\t'))
    page = next((r for r in records if r['level'] == '1'), None)
    if not page:
        return []
    width, height = float(page['width']), float(page['height'])
    if width <= 0 or height <= 0:
        return []
    lines = {}
    for row in records:
        if row['level'] != '5' or not row.get('text', '').strip() or float(row['conf']) < 0:
            continue
        key = tuple(row[k] for k in ('page_num', 'block_num', 'par_num', 'line_num'))
        lines.setdefault(key, []).append(row)
    result = []
    for words in lines.values():
        left = min(float(w['left']) for w in words)
        top = min(float(w['top']) for w in words)
        right = max(float(w['left']) + float(w['width']) for w in words)
        bottom = max(float(w['top']) + float(w['height']) for w in words)
        result.append({'text': ' '.join(w['text'] for w in words),
                       'confidence': min(float(w['conf']) for w in words) / 100,
                       'x': left / width, 'y': 1 - bottom / height,
                       'width': (right - left) / width, 'height': (bottom - top) / height})
    return result


async def run(*args: str, timeout=20) -> bytes:
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise ValueError('Frame extraction or scoreboard OCR failed')
    return stdout


async def frame_clock(segment: Path, offset: float, executable: str) -> list[dict]:
    frame = segment.with_suffix('.jpg')
    try:
        await run(ffmpeg_bin(), '-v', 'error', '-y', '-i', str(segment), '-ss', str(offset),
                  '-frames:v', '1', str(frame))
        if not frame.is_file():
            return []
        if executable == 'tesseract':
            return tesseract_rows((await run('tesseract', str(frame), 'stdout', '-l', 'eng',
                                             '--psm', '11', 'tsv')).decode())
        return json.loads(await run(executable, str(frame)))
    finally:
        frame.unlink(missing_ok=True)


# --- Baseball scorebug -------------------------------------------------------------
# Baseball has no game clock. The alignment signature is the matchup (batter +
# pitcher), the pitcher's cumulative "P:" count and the ball-strike count. NBC's bug
# draws the inning arrow, outs and bases as graphics that OCR rarely reads, so those
# are reported only when legible and never required.

PITCH_COUNT = re.compile(r'(?<![A-Z0-9])(?:P|PC|PITCHES)\s*[:;.]\s*(\d{1,3})(?!\d)')
BALL_STRIKE = re.compile(r'(?<![\d.-])([0-3])\s*-\s*([0-2])(?![\d-])')
INNING = re.compile(r'(?:(▲|▼|TOP|BOT|BOTTOM|MID|END)\s*(\d{1,2})(?:ST|ND|RD|TH)?)(?!\d)')
OUTS = re.compile(r'(?<!\d)([0-2])\s*OUTS?\b')
NOT_NAMES = {'OPS', 'AVG', 'ERA', 'OBP', 'SLG', 'HR', 'RBI', 'OUT', 'OUTS', 'TOP', 'BOT', 'MID', 'END',
             'BALL', 'STRIKE', 'PITCHES', 'LIVE', 'GAME', 'WILD', 'CARD', 'BEST', 'FINAL'}
# NBC swaps "PITCHER P:n" for e.g. "FOUR SEAM 99 MPH" ~1s after every delivery.
PITCH_GRAPHIC = re.compile(r'(FOUR[ -]?SEAM|TWO[ -]?SEAM|FASTBALL|SINKER|CUTTER|SLIDER|SWEEPER|SLURVE|CURVE(?:BALL)?|'
                           r'KNUCKLE[ -]?CURVE|CHANGE[ -]?UP|SPLITTER|SPLIT[ -]?FINGER|FORKBALL|KNUCKLEBALL|SCREWBALL|EEPHUS)'
                           r'\s*(\d{2,3})\s*MPH')
COUNT_GAP = .03        # the ball-strike count is at least this far right of any name
LINE_TOLERANCE = .025   # normalized height; one scorebug text line
BUG_WIDTH = .40         # a scorebug row never spans more than this much of the frame


def normalize_name(value: str) -> str:
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Z]', '', value.upper())


def _name_score(token: str, last: str) -> float:
    if not token or not last:
        return 0.
    if token == last:
        return 1.
    # OCR glues separators or neighbouring letters onto a name ("ISCHLITTLER").
    if len(last) >= 5 and last in token and len(token) - len(last) <= 2:
        return .95
    if min(len(token), len(last)) < 5:
        return 0.   # "RICE"/"RICO": short names must be exact
    return difflib.SequenceMatcher(None, token, last).ratio()


def match_player(token: str, roster: list[dict], threshold=.84) -> tuple[dict | None, bool]:
    """Best roster player for an OCR token -> (player, ambiguous)."""
    token = normalize_name(token)
    scored = sorted(((_name_score(token, normalize_name(p['last'])), p) for p in roster),
                    key=lambda item: -item[0])
    if not scored or scored[0][0] < threshold:
        return None, False
    best, player = scored[0]
    # Two players with the same (or nearly the same) surname cannot be told apart.
    rivals = [p for score, p in scored[1:] if p['id'] != player['id'] and score >= threshold
              and best - score < .05]
    return (None, True) if rivals else (player, False)


def _tokens(row: dict):
    """Name-like words (and 2-3 word runs, for "De La Cruz") with an estimated x position."""
    text = row['text']
    length = max(len(text), 1)
    words = [(m[0].strip(".'’-"), m.start()) for m in re.finditer(r"[^\W\d_][^\W\d_'’.\-]*", text)]
    for size in (1, 2, 3):
        for index in range(len(words) - size + 1):
            run = words[index:index + size]
            joined = ' '.join(w for w, _ in run)
            key = normalize_name(joined)
            if len(key) < 3 or (size == 1 and key in NOT_NAMES) or any(normalize_name(w) in NOT_NAMES for w, _ in run):
                continue
            yield joined, row['x'] + row.get('width', 0) * run[0][1] / length


def read_mlb_scorebug(rows: list[dict], roster: list[dict]) -> dict | None:
    """Parse a baseball scorebug from OCR rows (normalized, bottom-left origin).

    roster: [{'id', 'name', 'last', 'team'}] from bigplays.ingest.plays.mlb_roster.
    Returns {'batter', 'batter_id', 'pitcher', 'pitcher_id', 'pitch_count', 'balls',
    'strikes', 'outs', 'inning', 'inning_half', 'ambiguous'} or None when no single
    scorebug line with a pitch count is visible. Fields that are not legible are None.
    """
    states = []
    for anchor in rows:
        found = PITCH_COUNT.search(anchor['text'].upper())
        if not found or anchor.get('confidence', 1) < .5:
            continue
        line = [r for r in rows if abs(r['y'] - anchor['y']) < LINE_TOLERANCE
                and abs(r['x'] - anchor['x']) < BUG_WIDTH]
        anchor_x = anchor['x'] + anchor.get('width', 0) * found.start() / max(len(anchor['text']), 1)
        names, ambiguous, name_end = [], False, 0.
        for row in line:
            for word, x in _tokens(row):
                player, unclear = match_player(word, roster)
                ambiguous |= unclear
                if player or unclear:
                    name_end = max(name_end, x + row.get('width', 0) * len(word) / max(len(row['text']), 1))
                if player and all(player['id'] != p['id'] for p, _ in names):
                    names.append((player, x))
        # The count sits at the bug's right edge. A batter's game line ("0-1" = 0 for 1)
        # hugs his name, so candidates must be clear of every name, and the rightmost wins.
        candidates = []
        for row in line:
            text = row['text'].upper()
            if row is anchor:
                text = text[:found.start()] + ' ' * (found.end() - found.start()) + text[found.end():]
            for m in BALL_STRIKE.finditer(text.replace('O', '0')):
                x = row['x'] + row.get('width', 0) * m.start() / max(len(text), 1)
                if x >= name_end + COUNT_GAP:
                    candidates.append((x, int(m[1]), int(m[2])))
        counts = set()
        if candidates:
            right = max(candidates)[0]
            counts = {(b, st) for x, b, st in candidates if right - x < .03}
        # The pitcher's name sits immediately before "P:" (NBC puts the away team's
        # player on the left, so the batter may be on either side).
        left = [item for item in names if item[1] < anchor_x]
        pitcher = max(left, key=lambda item: item[1])[0] if left else None
        others = [p for p, _ in names if pitcher is None or p['id'] != pitcher['id']]
        batter = others[0] if len(others) == 1 else None
        if len(others) > 1:
            ambiguous = True
        if ambiguous:
            # A name that fits two players could be either slot: identify nobody.
            batter = pitcher = None
        if batter and pitcher and batter.get('team') and batter.get('team') == pitcher.get('team'):
            batter = pitcher = None
            ambiguous = True
        # Inning/outs are graphics on NBC; parse only explicit text near the bug.
        band = [r for r in rows if abs(r['y'] - anchor['y']) < .08 and abs(r['x'] - anchor['x']) < BUG_WIDTH]
        band_text = ' '.join(r['text'].upper() for r in band)
        innings = {(m[1], int(m[2])) for m in INNING.finditer(band_text)}
        outs = {int(m[1]) for m in OUTS.finditer(band_text)}
        inning = innings.pop() if len(innings) == 1 else None
        states.append({
            'league': 'mlb',
            'batter': batter and batter['name'], 'batter_id': batter and batter['id'],
            'pitcher': pitcher and pitcher['name'], 'pitcher_id': pitcher and pitcher['id'],
            'pitch_count': int(found[1]),
            'balls': next(iter(counts))[0] if len(counts) == 1 else None,
            'strikes': next(iter(counts))[1] if len(counts) == 1 else None,
            'outs': outs.pop() if len(outs) == 1 else None,
            'inning': inning and inning[1],
            'inning_half': inning and {'▲': 'Top', 'TOP': 'Top', '▼': 'Bottom', 'BOT': 'Bottom',
                                       'BOTTOM': 'Bottom', 'MID': 'Middle', 'END': 'End'}[inning[0]],
            'ambiguous': ambiguous or len(counts) > 1,
        })
    if not states:
        return _pitch_graphic(rows, roster)
    # Two different pitch counts on screen (e.g. a split screen) is not one scorebug.
    if len({s['pitch_count'] for s in states}) != 1:
        return None
    return max(states, key=lambda s: sum(v is not None for v in s.values()))


def _pitch_graphic(rows: list[dict], roster: list[dict]) -> dict | None:
    """The post-delivery pitch type/velocity graphic: marks that a pitch was just thrown."""
    graphics = []
    for row in rows:
        found = PITCH_GRAPHIC.search(re.sub(r'\s+', ' ', row['text'].upper()))
        if found and 40 <= int(found[2]) <= 110 and row.get('confidence', 1) >= .5:
            graphics.append((row, found))
    if len(graphics) != 1:
        return None
    anchor, found = graphics[0]
    line = [r for r in rows if abs(r['y'] - anchor['y']) < LINE_TOLERANCE and abs(r['x'] - anchor['x']) < BUG_WIDTH]
    names, ambiguous = {}, False
    for row in line:
        text = row['text'].upper()
        if row is anchor:
            row = row | {'text': text[:found.start()] + ' ' * (found.end() - found.start()) + text[found.end():]}
        for word, _ in _tokens(row):
            player, unclear = match_player(word, roster)
            ambiguous |= unclear
            if player:
                names[player['id']] = player
    batter = next(iter(names.values())) if len(names) == 1 and not ambiguous else None
    return {'league': 'mlb', 'pitch_graphic': True, 'pitch_type': found[1], 'velocity': int(found[2]),
            'batter': batter and batter['name'], 'batter_id': batter and batter['id'],
            'pitcher': None, 'pitcher_id': None, 'pitch_count': None, 'balls': None, 'strikes': None,
            'outs': None, 'inning': None, 'inning_half': None, 'ambiguous': ambiguous or len(names) > 1}
