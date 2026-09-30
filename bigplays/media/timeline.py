"""Archive original relayed HLS segments with their broadcast UTC timestamps."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from bigplays.media.ffmpeg_utils import ffmpeg_bin


def timestamp(value: str) -> float:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')
    return dt.timestamp()


def iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    temp.replace(path)


def relay_target(base: str, value: str) -> str:
    # Never turn a playlist supplied URL into a new outbound destination.
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.path != '/api/hls' or not parsed.query or parsed.fragment:
        raise ValueError('Archive accepts only signed resolver relay paths')
    return base.rstrip('/') + value


def parse_playlist(text: str) -> list[dict]:
    cursor = None
    duration = None
    sequence = 0
    discontinuity = False
    result = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(('#EXT-X-KEY:', '#EXT-X-MAP:', '#EXT-X-BYTERANGE:')):
            raise ValueError('Timestamp archive currently requires unencrypted MPEG-TS segments')
        if line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
            sequence = int(line.split(':', 1)[1])
        elif line.startswith('#EXT-X-DISCONTINUITY:') or line == '#EXT-X-DISCONTINUITY':
            discontinuity = True
            cursor = None  # Require a fresh time anchor across timestamp resets.
        elif line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
            cursor = timestamp(line.split(':', 1)[1])
        elif line.startswith('#EXTINF:'):
            duration = float(line.split(':', 1)[1].split(',')[0])
        elif line and not line.startswith('#'):
            if cursor is None or duration is None or not 0 < duration <= 60:
                raise ValueError('Stream has no reliable program-date-time mapping')
            result.append({'start': cursor, 'duration': duration, 'sequence': sequence,
                           'discontinuity': discontinuity, 'uri': line})
            cursor += duration
            sequence += 1
            duration = None
            discontinuity = False
    return result


def unique_segments(segments: list[dict]) -> list[dict]:
    # Sliding playlists re-anchor PDT and can differ by a millisecond of rounding.
    # The same media sequence must not be concatenated twice.
    result = []
    by_sequence = {}
    for segment in sorted(segments, key=lambda s: s['start']):
        previous = by_sequence.get(segment.get('sequence'))
        if previous and abs(previous['start'] - segment['start']) < .15:
            continue
        result.append(segment)
        by_sequence[segment.get('sequence')] = segment
    return result


def select_window(segments: list[dict], start: float, end: float) -> list[dict]:
    chosen = sorted((s for s in unique_segments(segments) if s['start'] < end and s['start'] + s['duration'] > start),
                    key=lambda s: s['start'])
    covered = start
    for segment in chosen:
        if segment['start'] > covered + 0.15:
            raise ValueError('Requested play crosses a missing section of the archive')
        covered = max(covered, segment['start'] + segment['duration'])
    if not chosen or covered < end - 0.15:
        raise ValueError('Requested play is not fully buffered yet or has expired')
    return chosen


class TimelineArchive:
    def __init__(self, directory: Path, base: str, retention_minutes=30, max_bytes=2 * 1024**3):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.base = base
        self.retention = retention_minutes * 60
        self.max_bytes = max_bytes
        self.playlist = None
        self.segments = []
        index = directory / 'index.json'
        if index.exists():
            original = [s for s in json.loads(index.read_text()) if (directory / s['file']).is_file()]
            self.segments = unique_segments(original)
            retained = {s['file'] for s in self.segments}
            for segment in original:
                if segment['file'] not in retained:
                    (directory / segment['file']).unlink(missing_ok=True)
            for segment in self.segments:
                segment.setdefault('bytes', (directory / segment['file']).stat().st_size)

    async def refresh(self, client: httpx.AsyncClient, root: str) -> list[dict]:
        target = self.playlist or root
        for _ in range(4):
            response = await client.get(relay_target(self.base, target))
            response.raise_for_status()
            text = response.text
            if not text.startswith('#EXTM3U'):
                raise ValueError('Expected HLS playlist')
            if '#EXT-X-STREAM-INF:' not in text:
                break
            target = next(line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#'))
        else:
            raise ValueError('Too many nested playlists')
        self.playlist = target
        listed = parse_playlist(text)
        known = {s['file'] for s in self.segments}
        new = []

        async def download(segment):
            name = f"{round(segment['start'] * 1000)}-{segment['sequence']}.ts"
            if name in known or any(s['sequence'] == segment['sequence'] and abs(s['start'] - segment['start']) < .15
                                    for s in self.segments):
                return
            response = await client.get(relay_target(self.base, segment['uri']))
            response.raise_for_status()
            if not response.content or response.content[0] != 0x47:
                raise ValueError('Expected unwrapped MPEG-TS from resolver')
            path = self.directory / name
            part = path.with_suffix('.part')
            part.write_bytes(response.content)
            part.replace(path)
            new.append({k: v for k, v in segment.items() if k != 'uri'} | {'file': name, 'bytes': len(response.content)})

        # A live playlist is short; cap parallel fetches even if a provider expands it.
        for offset in range(0, len(listed), 4):
            results = await asyncio.gather(*(download(s) for s in listed[offset:offset + 4]), return_exceptions=True)
            self.segments.extend(new)
            new.clear()
            self.segments.sort(key=lambda s: s['start'])
            atomic_json(self.directory / 'index.json', self.segments)
            for result in results:
                if isinstance(result, Exception):
                    raise result
        horizon = (self.segments[-1]['start'] if self.segments else 0) - self.retention
        expired = [s for s in self.segments if s['start'] + s['duration'] < horizon]
        self.segments = [s for s in self.segments if s not in expired]
        total = sum(s['bytes'] for s in self.segments)
        while total > self.max_bytes and len(self.segments) > 1:
            old = self.segments.pop(0)
            expired.append(old)
            total -= old['bytes']
        for segment in expired:
            (self.directory / segment['file']).unlink(missing_ok=True)
        atomic_json(self.directory / 'index.json', self.segments)
        return [s for s in self.segments if s['file'] not in known]

    async def cut(self, start: float, end: float, out: Path, metadata: dict, *, still_approved=None):
        selected = select_window(self.segments, start, end)
        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = self.directory / 'cut.txt'
        manifest.write_text(''.join(f"file '{(self.directory / s['file']).resolve()}'\n" for s in selected))
        temp = out.with_suffix('.partial.mp4')
        try:
            process = await asyncio.create_subprocess_exec(
                ffmpeg_bin(), '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(manifest),
                '-ss', str(max(0, start - selected[0]['start'])), '-t', str(end - start),
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-c:a', 'aac',
                '-movflags', '+faststart', str(temp), stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), 90)
            except BaseException:
                process.kill()
                await process.wait()
                raise
            if process.returncode:
                raise ValueError('FFmpeg could not encode requested play window')
            if still_approved is not None and not still_approved():
                raise ValueError('Play approval changed during encoding')
            temp.replace(out)
            atomic_json(out.with_suffix('.json'), metadata)
        finally:
            manifest.unlink(missing_ok=True)
            temp.unlink(missing_ok=True)


# --- Baseball (pitch-signature) alignment ------------------------------------------
# A baseball play has no game clock. It is located by the scorebug state shown just
# before its final pitch -- batter, pitcher, the pitcher's completed-pitch count
# ("P:", i.e. ESPN's pitch number minus one) and the ball-strike count -- followed by
# the on-screen transition to P:N. The pitcher's cumulative count never repeats within
# a game, so the signature is unique; anything contradictory returns None.

# Defaults measured on NBC footage of BOS@NYY 2026-09-29 (data/validation/mlb/alignment-report.json):
# the pitch-type/velocity graphic replaces "P:" about 1.2s after release, so the last
# pre-pitch frame is at most ~1.2s after release; the new "P:" appears 5-7s after release;
# Rice's double (contact 00:30:10.8) ended with his slide into second 8s after contact.
MLB_PRE_ROLL = 5.0        # seconds of set/wind-up kept before the earliest possible release
MLB_DISPLAY_LAG = 2.5     # the pre-pitch bug can persist this long after release (1.2s seen)
MLB_MAX_BRACKET = 30.0    # longest scorebug gap between last pre-pitch and first post-pitch frame
MLB_OFFSET_RANGE = (-180.0, 1200.0)  # video time minus ESPN wallclock (+12..+27s seen)
# Seconds kept after the first post-pitch frame (itself 5-7s after release), by result type.
MLB_POST_ROLL = {'home-run': 30.0, 'triple': 20.0, 'double': 15.0, 'single': 10.0,
                 'strikeout': 6.0, 'walk': 6.0, 'intent-walk': 6.0, 'hit-by-pitch': 6.0}
MLB_POST_ROLL_IN_PLAY = 10.0   # any other ball in play (outs, errors, sacrifices...)
MLB_POST_ROLL_DEFAULT = 6.0
MLB_GRAPHIC_WAIT = 8.0       # P:N normally follows the graphic within ~5s
MLB_GRAPHIC_TO_POST = 5.0     # graphic (release+~1s) to new "P:" (release+5-7s)
IN_PLAY_WORDS = ('ball in play', 'grounded', 'flied', 'lined', 'popped', 'fouled out', 'sacrifice', 'error', "fielder's choice",
                 'double play', 'triple play', 'reached')


def mlb_post_roll(play: dict) -> float:
    kind = play.get('event_type') or ''
    if kind in MLB_POST_ROLL:
        return MLB_POST_ROLL[kind]
    text = (play.get('text') or '').lower()
    if 'homered' in text or 'home run' in text:
        return MLB_POST_ROLL['home-run']
    if any(word in text for word in IN_PLAY_WORDS):
        return MLB_POST_ROLL_IN_PLAY
    return MLB_POST_ROLL_DEFAULT


def _mlb_legible(o: dict) -> bool:
    return o.get('pitcher_id') is not None and o.get('pitch_count') is not None


NO_PITCH_SIGNATURE = 'not alignable from video: no pitch signature'
MLB_MAX_PITCH_OFFSET = 2   # broadcast "P:" may differ from ESPN's count by this much


def _signature_offsets(pitch: dict, frames: list[dict], pitcher: str) -> set[int]:
    """Offsets k for which pitch n's pre-pitch state (P:n-1+k, batter, count) is on screen."""
    return {o['pitch_count'] - (pitch['n'] - 1) for o in frames
            if o['pitcher_id'] == pitcher and o.get('batter_id') == pitch.get('batter_id')
            and pitch.get('batter_id') and not o.get('ambiguous')
            and (o.get('balls'), o.get('strikes')) == (pitch.get('balls'), pitch.get('strikes'))
            and abs(o['pitch_count'] - (pitch['n'] - 1)) <= MLB_MAX_PITCH_OFFSET}


def align_mlb_play(play: dict, observations: list[dict], **options) -> dict:
    """Locate an MLB play's final pitch; tolerant of a constant broadcast "P:" offset.

    Exact pitch numbers are tried first. If ESPN's count and the broadcast's differ by a
    small constant for this pitcher (ESPN dropped or merged a pitch), the offset is
    learned from the pitcher's most recent earlier pitch whose full signature is on
    screen, and accepted only if the same offset explains this play. The window then
    carries 'pitch_offset'. See _align_mlb_exact for the matching rules.
    """
    exact = _align_mlb_exact(play, observations, **options)
    if exact['window'] is not None or 'not on screen yet' not in exact['reason']:
        if exact['window'] is not None:
            exact['window']['pitch_offset'] = 0
        return exact
    number, pitcher = play['pitcher_pitch_count'], play['pitcher_id']
    low, high = MLB_OFFSET_RANGE
    frames = [o for o in observations if _mlb_legible(o) and o.get('pitcher_id') == pitcher
              and low <= o['time'] - play['occurred'] <= high]
    here = _signature_offsets({'n': number, 'batter_id': play.get('batter_id'), 'balls': play.get('balls_before'),
                               'strikes': play.get('strikes_before')}, frames, pitcher) - {0}
    if not here:
        return exact
    learned = None
    for earlier in reversed(play.get('recent_pitches') or []):
        seen = _signature_offsets(earlier, frames, pitcher)
        if seen:
            learned = seen
            break
    if learned is None or len(learned) != 1 or len(here) != 1 or learned != here:
        return {'window': None, 'reason': f"on-screen P: is off from ESPN's pitch number by {sorted(here)}, "
                                          f"not confirmed by this pitcher's earlier pitches ({sorted(learned or [])})"}
    offset = here.pop()
    shifted = _align_mlb_exact(play | {'pitcher_pitch_count': number + offset}, observations, **options)
    if shifted['window'] is not None:
        shifted['window']['pitch_offset'] = offset
        shifted['window']['signature'] += f' (broadcast P: = ESPN {offset:+d})'
    return shifted


def _align_mlb_exact(play: dict, observations: list[dict], *, pre_roll: float = MLB_PRE_ROLL,
                     display_lag: float = MLB_DISPLAY_LAG, max_bracket: float = MLB_MAX_BRACKET,
                     post_roll: float | None = None) -> dict:
    """Locate an MLB play's final pitch in timestamped scorebug observations.

    play: parsed by bigplays.ingest.plays.parse_mlb_plays (needs pitcher_id, batter_id,
    pitcher_pitch_count, balls_before/strikes_before, occurred).
    observations: read_mlb_scorebug() dicts plus 'time' (buffer UTC seconds).
    Returns {'window': {...} | None, 'reason': str}. The window holds 'start'/'end'
    (UTC seconds to cut), 'time' (first post-pitch frame, the anchor), the release
    bounds 'release_earliest'/'release_latest', 'closed_by' ('transition' or
    'pitch graphic'), frame counts and 'offset_seconds' (video minus ESPN wallclock).
    """
    def refuse(reason, **evidence):
        return {'window': None, 'reason': reason, **evidence}

    number = play.get('pitcher_pitch_count')
    pitcher, batter = play.get('pitcher_id'), play.get('batter_id')
    balls, strikes = play.get('balls_before'), play.get('strikes_before')
    if not number or not pitcher or not batter or balls is None or strikes is None:
        return refuse(NO_PITCH_SIGNATURE)
    if play.get('pitch_count_consistent') is False:
        return refuse("ESPN pitch sequence disagrees with the box score pitch count")
    low, high = MLB_OFFSET_RANGE
    frames = sorted((o for o in observations if _mlb_legible(o) and o.get('pitcher_id') == pitcher
                     and low <= o['time'] - play['occurred'] <= high), key=lambda o: o['time'])
    same_count = [o for o in frames if o['pitch_count'] == number - 1]
    pre = [o for o in same_count if o.get('batter_id') == batter and o.get('balls') == balls
           and o.get('strikes') == strikes and not o.get('ambiguous')]
    if not pre:
        return refuse(f'pre-pitch signature P:{number - 1} {balls}-{strikes} not on screen yet')

    def forward(o):
        # After the signature NBC may reset the count to 0-0 (plate appearance over) or
        # advance it before "P:" increments: that is the transition, not a contradiction.
        b, st = o.get('balls'), o.get('strikes')
        return (o.get('batter_id') in (None, batter) and b is not None and st is not None
                and ((b, st) == (0, 0) or (b >= balls and st >= strikes)))

    # The signature span ends at its last frame, or earlier at pitch N's graphic (a
    # stale pre-pitch bug can reappear after the graphic).
    boundary = min([pre[-1]['time']] + [o['time'] for o in observations if o.get('pitch_graphic')
                    and not o.get('ambiguous') and o.get('batter_id') in (None, batter) and o['time'] > pre[0]['time']])
    # A legible frame at P:N-1 that shows a different batter or count contradicts ESPN,
    # unless it comes after the signature span and only moves the count forward.
    conflicts = [o for o in same_count if o not in pre and not o.get('ambiguous') and (
        (o.get('batter_id') not in (None, batter)) or
        (o.get('balls') is not None and o.get('strikes') is not None and (o['balls'], o['strikes']) != (balls, strikes)))
        and not (o['time'] > boundary and forward(o))]
    advanced = [o for o in same_count if o not in pre and o['time'] > boundary and forward(o)
                and (o['balls'], o['strikes']) != (balls, strikes)]
    if conflicts:
        return refuse('scorebug contradicts the pre-pitch signature', conflicts=[o['time'] for o in conflicts])
    last_pre = pre[-1]['time']
    post = [o for o in frames if o['pitch_count'] == number]
    if any(o['time'] < last_pre for o in post):
        return refuse('post-pitch count seen before the pre-pitch signature (ambiguous)')
    earlier = [o for o in frames if o['pitch_count'] < number - 1 and o['time'] > pre[0]['time']]
    if earlier:
        return refuse('pitch count went backwards during the signature (ambiguous)')
    # The pitch type/velocity graphic replaces "P:" about a second after release. Pitch
    # N-1's graphic precedes the first P:N-1 frame, so one after that is pitch N's --
    # even if a stale P:N-1 bug reappears after it (seen after an inning-ending out).
    graphics = [o for o in observations if o.get('pitch_graphic') and not o.get('ambiguous')
                and o.get('batter_id') in (None, batter) and o['time'] > pre[0]['time']
                and (not post or o['time'] < post[0]['time'])]
    if graphics:
        last_pre = min(last_pre, graphics[0]['time'])
    if post:
        first_post = post[0]['time']
        confirmations = len(post) + sum(o['pitch_count'] > number and o['time'] > first_post for o in frames)
        if confirmations < 2:
            return refuse('waiting for a second frame confirming the transition')
        closed_by, end_anchor = 'transition', first_post
        if graphics and graphics[0]['time'] + MLB_GRAPHIC_TO_POST < first_post:
            # P:N may only return much later (next inning, after a break): the
            # delivery is already pinned by its graphic, so do not run on to it.
            end_anchor = graphics[0]['time'] + MLB_GRAPHIC_TO_POST
    elif graphics:
        # No P:N yet (e.g. the bug left for a break after a third out). The graphic
        # alone closes the pitch only once later, different scorebug state is seen.
        after = [o for o in observations if _mlb_legible(o) and o['time'] > graphics[0]['time']
                 and not (o['pitcher_id'] == pitcher and o['pitch_count'] == number - 1)]
        # Close from the graphic without waiting for a break to end when the play ends
        # the half-inning (P:N is usually never shown), or once P:N is overdue.
        ends_half = play.get('outs') == 3
        overdue = max(o['time'] for o in observations) >= graphics[0]['time'] + MLB_GRAPHIC_WAIT
        if not (after or ends_half or overdue):
            return refuse(f'waiting for transition to P:{number}')
        first_post = after[0]['time'] if after else graphics[0]['time']
        closed_by = 'pitch graphic' + ('' if after else ' (half-inning over)' if ends_half else ' (P:N overdue)')
        end_anchor = graphics[0]['time'] + MLB_GRAPHIC_TO_POST
    else:
        return refuse(f'waiting for transition to P:{number}')
    release_latest = min([first_post] + [o['time'] for o in graphics + advanced])
    if release_latest - last_pre > max_bracket:
        return refuse(f'scorebug hidden {release_latest - last_pre:.0f}s around the pitch; cannot bracket it')
    release_earliest = max(pre[0]['time'], last_pre - display_lag)
    start = release_earliest - pre_roll
    end = end_anchor + (mlb_post_roll(play) if post_roll is None else post_roll)
    return {'window': {'start': start, 'end': end, 'time': end_anchor, 'release_earliest': release_earliest,
                       'release_latest': release_latest, 'pre_frames': len(pre), 'post_frames': len(post),
                       'graphic_frames': len(graphics), 'closed_by': closed_by,
                       'last_pre_time': last_pre, 'offset_seconds': release_latest - play['occurred'],
                       'signature': f"P:{number - 1} {balls}-{strikes} -> P:{number}",
                       'period': play.get('inning'), 'clock': ''},
            'reason': 'aligned'}
