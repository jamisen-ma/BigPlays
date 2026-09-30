"""Persistent discovery, timestamped DVR, and clock-aligned play clipping."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import inspect
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import httpx

from bigplays.config import gate_mode, settings
from bigplays.ingest.live_streams import discover
from bigplays.ingest import plays as play_feed
from bigplays.ingest.plays import SUMMARY_PATHS, locate_play, parse_plays, read_scorebug, team_aliases
from bigplays.media.scoreboard import frame_clock, run
from bigplays.media.stream_buffer import LiveSegmentBuffer
from bigplays.media.timeline import atomic_json, iso, select_window, timestamp
from bigplays.storage.local_store import ClipLibrary
from bigplays.ingest.reddit import RedditClient, configuration_status
from bigplays.ingest.reddit_browser import BrowserRedditClient
from bigplays.orchestrator.social_ranker import SocialMonitor, SocialJudge, play_fingerprint
from bigplays.orchestrator.hype_gate import HypeGate, HypeJudge, cleanup_pending, fan_hype_record, pending_paths

log = logging.getLogger('live-agent')

# Leagues that discovery never hands to a GameMonitor. MLB stays excluded until
# the timing agent's MLB alignment is enabled; to enable MLB live capture,
# change this line to:  DISCOVERY_EXCLUDED_LEAGUES = frozenset()
DISCOVERY_EXCLUDED_LEAGUES = frozenset()

# Re-resolve the stream when no new segment has arrived for this long.
STALL_SECONDS = 45
# Give up waiting for a window that ended this long ago (frees its retention pin).
PIN_EXPIRY_SECONDS = 3600
# Rejected held clips are removed after SOCIAL_HYPE_PENDING_TTL_HOURS; checked this often.
PENDING_CLEANUP_SECONDS = 600


def mlb_alignment():
    """The timing agent's MLB hooks, or None when MLB alignment is not available yet.

    Seam contract (bigplays/ingest/plays.py, owned by the timing agent):
      * read_scorebug(rows, aliases, league='mlb') -> observation dict | None
      * locate_mlb_play(play, observations) -> window | None, or
        locate_play(play, observations, league='mlb') -> window | None
    A window is {'start': utc, 'end': utc, ...} (a clip window) or an anchor
    {'time': utc, ...} which gets the configured pre/post roll.
    """
    locate = getattr(play_feed, 'locate_mlb_play', None)
    if locate is None:
        try:
            if 'league' in inspect.signature(play_feed.locate_play).parameters:
                def locate(play, observations):
                    return play_feed.locate_play(play, observations, league='mlb')
        except (TypeError, ValueError):
            locate = None
    if locate is None or 'league' not in inspect.signature(play_feed.read_scorebug).parameters:
        return None

    def read(rows, aliases):
        return play_feed.read_scorebug(rows, aliases, league='mlb')
    return read, locate


def normalize_window(found, play):
    """Turn a locate result into {'start','end','anchor','alignment'} or None."""
    if not found:
        return None
    if isinstance(found, (tuple, list)) and len(found) == 2:
        found = {'start': found[0], 'end': found[1]}
    if not isinstance(found, dict):
        return None
    start = found.get('start', found.get('clip_start'))
    end = found.get('end', found.get('clip_end'))
    anchor = found.get('time', found.get('anchor'))
    if start is None or end is None:
        if anchor is None:
            return None
        start = anchor - settings.agent_pre_roll_seconds
        end = anchor + settings.agent_post_roll_seconds
    if anchor is None:
        anchor = start + settings.agent_pre_roll_seconds if end - start > settings.agent_pre_roll_seconds else start
    try:
        start, end, anchor = float(start), float(end), float(anchor)
    except (TypeError, ValueError):
        return None
    if not end > start or end - start > 300:
        return None  # refuse absurd windows rather than cutting minutes of footage
    return {'start': start, 'end': end, 'anchor': anchor, 'match': found}


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def clip_id(game, play_id):
    return hashlib.sha256(f"{game['league']}:{game['game_id']}:{play_id}".encode()).hexdigest()[:24]


class GameMonitor:
    def __init__(self, game, reddit=None, judge=None, hype_judge=None):
        self.game = game
        self.directory = settings.agent_dir / f"{game['league']}-{game['game_id']}"
        self.archive = LiveSegmentBuffer(self.directory, settings.resolver_base_url, settings.agent_buffer_minutes,
                                         settings.agent_buffer_max_mb * 1024**2,
                                         margin_seconds=settings.agent_pre_roll_seconds + 150)
        self.library = ClipLibrary(settings.clips_dir, settings.database_path)
        self.alignment = self.alignment_hooks(game['league'])
        self.window_known_at = {}
        self.reconnects = 0
        self.root = None
        self.resolved_at = 0
        self.plays = []
        self.aliases = []
        self.observations = load_json(self.directory / 'clocks.json', [])
        self.scanned = set(load_json(self.directory / 'scanned.json', []))
        self.error = None
        self.capture_error = None
        self.last_capture = None
        self.last_poll = None
        self.last_clip = None
        self.finished_at = None
        self.last_retry = {}
        # hype: cut, hold, publish on Reddit fan-hype approval (RSS store + local LLM).
        # off/legacy: the OAuth/browser SocialMonitor (post-publication enrichment or approve-before-cut).
        if gate_mode() == 'hype':
            self.social = HypeGate(self, hype_judge)
        else:
            self.social = SocialMonitor(self, reddit, judge) if reddit and judge else None

    def hype_gate(self) -> HypeGate | None:
        return self.social if gate_mode() == 'hype' and isinstance(self.social, HypeGate) else None

    def alignment_hooks(self, league):
        """League dispatch: (read_observation(rows, aliases), locate(play, observations)) or None."""
        if league == 'mlb':
            return mlb_alignment()  # None until the timing agent ships MLB alignment
        return (lambda rows, aliases: read_scorebug(rows, aliases)), locate_play

    def locate(self, play):
        if self.alignment is None:
            return None
        return normalize_window(self.alignment[1](play, self.observations), play)

    async def resolve(self, client):
        response = await client.post(settings.resolver_base_url.rstrip('/') + '/api/stream',
            json={'url': self.game['url']}, timeout=55,
            headers={'Authorization': 'Bearer ' + settings.resolver_api_key})
        response.raise_for_status()
        result = response.json()
        if not result.get('ok'):
            raise ValueError(f"{result.get('stage')}: {result.get('error')}")
        self.root = result['proxiedUrl']
        self.archive.playlist = None
        self.resolved_at = time.time()

    async def capture(self, client):
        """Keep the buffer filling: reconnect on errors, re-resolve on repeated failure or stall."""
        failures = 0
        backoff = 1.0
        while True:
            try:
                if not self.root or time.time() - self.resolved_at > 5 * 3600:
                    if self.root:
                        self.reconnects += 1
                    await self.resolve(client)
                await self.archive.refresh(client, self.root)
                self.last_capture = iso(time.time())
                if self.archive.stalled(minimum=STALL_SECONDS):
                    # Playlist answers but stopped advancing (frozen relay/origin).
                    log.warning('Stream stalled for game %s; re-resolving', self.game['game_id'])
                    self.capture_error = 'Stream stalled; reconnecting'
                    self.root = None
                    self.archive.last_new_segment_at = time.time()
                else:
                    self.capture_error = None
                failures = 0
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Do not log signed URLs, API secrets, or HTTP exception strings.
                self.capture_error = str(error) if isinstance(error, ValueError) else type(error).__name__
                failures += 1
                status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
                if status:
                    self.capture_error += f' ({status})'
                # A single failed segment fetch should not throw away a still-valid
                # playlist and spend its entire rewind window resolving again.
                # Expired/forbidden playlists (401/403/404/410) re-resolve at once.
                if failures >= 3 or isinstance(error, ValueError) or status in (401, 403, 404, 410):
                    self.root = None
                    failures = 0
                    await asyncio.sleep(min(backoff, 30))
                    backoff *= 2
                else:
                    await asyncio.sleep(1)
            await asyncio.sleep(2)

    async def scan(self):
        while True:
            try:
                if not self.aliases or self.alignment is None:
                    # No aliases yet, or a league whose alignment is not available (MLB): skip OCR safely.
                    await asyncio.sleep(1 if self.aliases else 30)
                    continue
                pending = [s for s in self.archive.segments if s['file'] not in self.scanned]
                # Scan each completed original segment, including the provider's initial rewind window.
                for segment in pending[:8]:
                    for offset in (min(1., segment['duration'] / 3), max(1., segment['duration'] - 1.)):
                        rows = await frame_clock(self.directory / segment['file'], offset, settings.scoreboard_ocr_bin)
                        reading = self.alignment[0](rows, self.aliases)
                        if reading:
                            self.observations.append(reading | {'time': segment['start'] + offset})
                    self.scanned.add(segment['file'])
                horizon = time.time() - settings.agent_buffer_minutes * 60 - 600
                self.observations = [o for o in self.observations if o['time'] > horizon]
                self.scanned.intersection_update(s['file'] for s in self.archive.segments)
                atomic_json(self.directory / 'clocks.json', self.observations)
                atomic_json(self.directory / 'scanned.json', sorted(self.scanned))
                if (self.error or '').startswith('Scoreboard alignment unavailable'):
                    self.error = None
            except Exception as error:
                self.error = 'Scoreboard alignment unavailable: ' + type(error).__name__
                await asyncio.sleep(5)
            await asyncio.sleep(1)

    async def poll_plays(self, client):
        while True:
            try:
                response = await client.get('https://site.api.espn.com/apis/site/v2/sports/' +
                    SUMMARY_PATHS[self.game['league']] + '/summary', params={'event': self.game['game_id']})
                response.raise_for_status()
                payload = response.json()
                self.plays = parse_plays(payload, self.game['league'])
                self.correct_play_metadata()
                self.aliases = team_aliases(payload)
                self.last_poll = iso(time.time())
                if (self.error or '').startswith('Play feed unavailable'):
                    self.error = None  # recovered; don't leave a stale failure in the status
                state = payload.get('header', {}).get('competitions', [{}])[0].get('status', {}).get('type', {}).get('state')
                if state == 'post' and self.finished_at is None:
                    self.finished_at = time.time()
                atomic_json(self.directory / 'plays.json', self.plays)
            except Exception as error:
                self.error = 'Play feed unavailable: ' + type(error).__name__
            await asyncio.sleep(max(5, settings.espn_poll_seconds))

    def correct_play_metadata(self):
        # ESPN can overturn an apparent score after the original clip was cut.
        for play in self.plays:
            path = settings.clips_dir / (clip_id(self.game, play['play_id']) + '.json')
            if not path.exists():
                continue
            metadata = load_json(path, {})
            if metadata.get('description') == play['text']:
                continue
            metadata.update(title=play['text'], description=play['text'], clock=play['clock'],
                            period=str(play['period']), home_score=play['home_score'], away_score=play['away_score'])
            if metadata.get('social_assessment'):
                metadata['social_assessment'] = {'status': 'play_corrected'}
                metadata['combined_score'] = metadata.get('base_score', .8)
            if metadata.get('event_id'):
                self.library.persist(metadata, new=False)
            else:
                atomic_json(path, metadata)
            if self.last_clip and self.last_clip['play_id'] == play['play_id']:
                self.last_clip['title'] = play['text']

    def request_path(self, play):
        return settings.agent_dir / 'requests' / f"{self.game['game_id']}-{play['play_id']}.json"

    def play_status(self, play):
        event_id = clip_id(self.game, play['play_id'])
        if self.library.saved(event_id):
            return 'clipped'
        gate = self.hype_gate()
        if gate and pending_paths(event_id)['json'].exists():
            return gate.gate_status(play) or 'held: waiting for Reddit reactions'
        if not self.request_path(play).exists():
            if not play['interesting']:
                return 'below initial play filter'
            if gate_mode() == 'legacy' and not (self.social and self.social.approved(play)):
                return self.social.gate_status(play) if self.social else 'waiting for fan reaction review'
        if not self.archive.segments:
            return 'waiting for video'
        # ESPN wallclock slack: football can be minutes off; MLB measured -5..+17s off the video.
        slack = 120 if self.game['league'] == 'mlb' else 600
        if play['occurred'] + slack < self.archive.segments[0]['start']:
            return 'outside recorded history'
        if self.alignment is None:
            return f"waiting for {self.game['league'].upper()} video alignment support"
        window = self.locate(play)
        if not window:
            if self.game['league'] == 'mlb' and (not play.get('batter') or play.get('pitcher_pitch_count') is None):
                return 'not alignable from video (no pitch signature)'
            return 'waiting for matching on-screen clock'
        if window['start'] < self.archive.segments[0]['start']:
            return 'outside recorded history'
        try:
            select_window(self.archive.segments, window['start'], window['end'])
            return 'ready to clip'
        except ValueError:
            if window['end'] < time.time() - 30:
                return 'missing recorded segments'
            return 'waiting for complete play window'

    async def clips(self):
        while True:
            for play in list(self.plays):
                try:
                    await self.clip_play(play)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.error = 'Play cut failed: ' + type(error).__name__
            now = time.time()
            for key, (_, end) in list(getattr(self.archive, 'pins', {}).items()):
                if end < now - PIN_EXPIRY_SECONDS:
                    self.archive.unpin(key)
            await asyncio.sleep(2)

    async def clip_play(self, play):
        """Cut, poster and persist one play as soon as its full window is buffered. Idempotent.

        With SOCIAL_CLIP_GATE=hype the cut is the same, but unless the play is can't-miss it is
        saved pending (hidden from the catalog, APIs and SSE) until the hype gate decides.
        """
        request = self.request_path(play)
        if not play['interesting'] and not request.exists():
            return
        event_id = clip_id(self.game, play['play_id'])
        out = settings.clips_dir / (event_id + '.mp4')
        if self.library.saved(event_id):
            request.unlink(missing_ok=True)
            return
        manual = request.exists()
        mode = gate_mode()
        gate = self.hype_gate()
        if gate and pending_paths(event_id)['json'].exists():
            review = gate.review_for(play)
            if manual:  # manual override publishes a held or rejected clip as it is
                self.publish_pending(play['play_id'], reasons=['manual_override'],
                                     fan_hype=fan_hype_record(review) if review.get('evidence') else None,
                                     decided_at=time.time())
                request.unlink(missing_ok=True)
            elif not review:
                gate.restore(play, load_json(pending_paths(event_id)['json'], {}))
            elif review.get('status') in ('approved', 'fallback'):  # safety net after an interrupted publish
                gate.publish_decided(play['play_id'], review)
            return
        if not manual and mode == 'legacy' and not (self.social and self.social.approved(play)):
            return
        window = self.locate(play)
        if not window:
            return
        start, end = window['start'], window['end']
        self.window_known_at.setdefault(event_id, time.time())
        if hasattr(self.archive, 'pin'):
            self.archive.pin(event_id, start, end)  # retention must not evict it while post-roll arrives
        if time.time() - self.last_retry.get(event_id, 0) < 10:
            return
        if not self.archive.segments or start < self.archive.segments[0]['start']:
            return
        try:
            select_window(self.archive.segments, start, end)
        except ValueError:
            return  # post-roll not buffered yet (or a gap): wait, never substitute newer footage
        self.last_retry[event_id] = time.time()
        match = window['match']
        admission = gate.admission(play) if gate and not manual else None
        hold = bool(admission and admission['hold'])
        if hold:
            out = pending_paths(event_id)['mp4']
        if manual:
            reasons = ['manual_override']
        elif admission:
            reasons = admission['reasons']
        else:
            reasons = ['initial_filter', 'fan_hype_approved'] if mode == 'legacy' else ['initial_filter']
        metadata = {'event_id': event_id, 'game_id': self.game['game_id'], 'league': self.game['league'],
            'name': self.game['name'], 'play_id': play['play_id'], 'source_play_id': play['play_id'],
            'source_kind': 'live_capture',
            'occurred_utc': iso(play['occurred']), 'event_time_utc': iso(play['occurred']),
            'clip_start_utc': iso(start), 'clip_end_utc': iso(end), 'title': play['text'],
            'description': play['text'], 'period': str(play['period']), 'clock': play.get('clock', ''),
            'home_score': play['home_score'], 'away_score': play['away_score'],
            'tags': ['play', 'clock-aligned'], 'reasons': reasons,
            'base_score': .8, 'combined_score': .8, 'file': out.name,
            'alignment': {'method': 'program-date-time + on-screen quarter/game clock' if self.game['league'] != 'mlb'
                          else 'program-date-time + MLB scorebug',
                          'video_time_utc': iso(window['anchor']), 'clock': match.get('clock'),
                          'period': match.get('period'), 'offset_seconds': window['anchor'] - play['occurred']}}
        for key in ('inning', 'inning_half', 'period_label', 'count', 'outs', 'event_type'):
            if play.get(key) not in (None, ''):
                metadata[key] = play[key]
        if not manual and mode == 'legacy':
            decision = self.social.decision_for(play)
            metadata.update(social_assessment=decision['assessment'], combined_score=decision['combined_score'])
        fingerprint = play_fingerprint(play)

        def still_approved():
            if manual or mode != 'legacy':
                return True
            current = next((p for p in self.plays if p['play_id'] == play['play_id']), None)
            return bool(current and play_fingerprint(current) == fingerprint and self.social.approved(current))

        try:
            info = await self.archive.cut(start, end, out, metadata, still_approved=still_approved) or {}
        except ValueError:
            return  # Retry missing/post-roll segments; never substitute the latest footage.
        saved = time.time()
        if info.get('clip_start') is not None:
            metadata['clip_start_utc'] = iso(info['clip_start'])
        if info.get('poster'):
            metadata['poster'] = info['poster']
        metadata['captured_utc'] = metadata['received_utc'] = iso(saved)
        metadata['capture'] = {
            'cut_mode': info.get('mode'), 'cut_seconds': info.get('cut_seconds'),
            'event_to_video_seconds': round(window['anchor'] - play['occurred'], 3),
            'event_to_clip_seconds': round(saved - play['occurred'], 3),
            'video_end_to_clip_seconds': round(saved - end, 3),
            'window_known_to_clip_seconds': round(saved - self.window_known_at.get(event_id, saved), 3)}
        if hold:
            metadata['pending'] = {'cut_at': saved, 'anchor': window['anchor'], 'fingerprint': fingerprint}
            atomic_json(pending_paths(event_id)['json'], metadata)
            if hasattr(self.archive, 'unpin'):
                self.archive.unpin(event_id)
            self.window_known_at.pop(event_id, None)
            gate.register(play, anchor=window['anchor'], cut_at=saved, held=True)
            log.info('Held play clip for fan reactions: game=%s play=%s event->cut=%.1fs', self.game['game_id'],
                     play['play_id'], metadata['capture']['event_to_clip_seconds'])
            return
        if admission:  # hype mode, published at cut: can't-miss play or no Reddit coverage
            metadata['fan_hype'] = admission['fan_hype']
            metadata['published_utc'] = iso(saved)
            metadata['capture'].update(cut_at=iso(saved), decision_at=iso(saved), published_at=iso(saved),
                                       event_to_publish_seconds=round(saved - play['occurred'], 3),
                                       decision_latency_seconds=0.0)
        record = self.library.persist(metadata)
        if hasattr(self.archive, 'unpin'):
            self.archive.unpin(event_id)
        self.window_known_at.pop(event_id, None)
        request.unlink(missing_ok=True)
        if admission and admission.get('cant_miss'):  # attach fan reactions later; never re-gated
            gate.register(play, anchor=window['anchor'], cut_at=saved, held=False,
                          cant_miss=admission['cant_miss'], text=admission['text'])
        self.last_clip = {'file': '/clips/' + out.name, 'play_id': play['play_id'], 'title': play['text'],
                          'event_to_clip_seconds': record['capture']['event_to_clip_seconds']}
        self.error = None
        log.info('Created play clip: game=%s play=%s mode=%s event->clip=%.1fs', self.game['game_id'],
                 play['play_id'], info.get('mode'), record['capture']['event_to_clip_seconds'])

    def publish_pending(self, play_id, *, reasons, fan_hype=None, decided_at=None):
        """Move a held clip into the library: files, sidecar and catalog row (-> SSE + game_update).

        Synchronous (no await), so it cannot interleave with another publish of the same clip.
        """
        event_id = clip_id(self.game, play_id)
        held, final = pending_paths(event_id), self.library.paths(event_id)
        if self.library.saved(event_id):
            for path in held.values():
                path.unlink(missing_ok=True)
            return None
        metadata = load_json(held['json'], None)
        if not isinstance(metadata, dict) or metadata.get('event_id') != event_id:
            return None
        for kind in ('jpg', 'mp4'):
            if held[kind].exists():
                os.replace(held[kind], final[kind])
        if not final['mp4'].exists():
            log.warning('Held clip video missing; cannot publish: game=%s play=%s', self.game['game_id'], play_id)
            return None
        now = time.time()
        decided = decided_at or now
        hold = metadata.pop('pending', None) or {}
        current = next((p for p in self.plays if p['play_id'] == play_id), None)
        if current and current['text'] != metadata.get('description'):  # ESPN corrected it while held
            metadata.update(title=current['text'], description=current['text'], home_score=current['home_score'],
                            away_score=current['away_score'], clock=current.get('clock', ''))
        metadata['reasons'] = reasons
        if fan_hype:
            metadata['fan_hype'] = fan_hype
        cut_at = float(hold.get('cut_at') or now)
        occurred = current['occurred'] if current else timestamp(metadata['occurred_utc'])
        metadata['received_utc'] = metadata['published_utc'] = iso(now)
        metadata.setdefault('capture', {}).update(
            cut_at=iso(cut_at), decision_at=iso(decided), published_at=iso(now),
            event_to_publish_seconds=round(now - occurred, 3), decision_latency_seconds=round(decided - cut_at, 3))
        record = self.library.persist(metadata)
        held['json'].unlink(missing_ok=True)
        self.last_clip = {'file': '/clips/' + final['mp4'].name, 'play_id': play_id, 'title': metadata['title'],
                          'event_to_clip_seconds': record['capture'].get('event_to_clip_seconds'),
                          'event_to_publish_seconds': record['capture']['event_to_publish_seconds']}
        log.info('Published held clip: game=%s play=%s reasons=%s event->publish=%.1fs', self.game['game_id'],
                 play_id, ','.join(reasons), record['capture']['event_to_publish_seconds'])
        return record

    def reject_pending(self, play_id, *, fan_hype=None, decided_at=None):
        """Keep a rejected clip hidden, stamped for TTL cleanup (manual override can still publish it)."""
        path = pending_paths(clip_id(self.game, play_id))['json']
        metadata = load_json(path, None)
        if not isinstance(metadata, dict):
            return
        metadata.setdefault('pending', {})['decision'] = {'status': 'rejected', 'at': decided_at or time.time(),
                                                          'text': (fan_hype or {}).get('decision')}
        if fan_hype:
            metadata['fan_hype'] = fan_hype
        atomic_json(path, metadata)

    def enrich_published(self, play_id, fan_hype):
        """Attach fan-hype evidence to an already published (can't-miss) clip; publication is unchanged."""
        event_id = clip_id(self.game, play_id)
        metadata = load_json(self.library.paths(event_id)['json'], None)
        if not isinstance(metadata, dict) or metadata.get('event_id') != event_id:
            return
        metadata['fan_hype'] = fan_hype
        self.library.persist(metadata, new=False)

    def status(self):
        return {'game': self.game, 'last_capture': self.last_capture, 'last_play_check': self.last_poll,
                'segments': len(self.archive.segments), 'clock_observations': len(self.observations),
                'buffer_start': iso(self.archive.segments[0]['start']) if self.archive.segments else None,
                'buffer_end': iso(self.archive.segments[-1]['start'] + self.archive.segments[-1]['duration']) if self.archive.segments else None,
                'error': self.capture_error or self.error, 'last_clip': self.last_clip,
                'buffer': self.archive.health() if hasattr(self.archive, 'health') else None,
                'reconnects': self.reconnects,
                'alignment_available': self.alignment is not None,
                'reddit': self.social.status if self.social else configuration_status(),
                'plays': [self.play_view(p) for p in self.plays[-12:]]}

    def play_view(self, play):
        view = play | {'status': self.play_status(play)}
        gate = self.hype_gate()
        if gate and gate.review_for(play):
            view['gate'] = gate.gate_status(play)
        return view

    async def run(self):
        async with httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False) as client:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.capture(client))
                group.create_task(self.poll_plays(client))
                group.create_task(self.scan())
                group.create_task(self.clips())
                if self.social:
                    group.create_task(self.social.run())


async def main():
    settings.agent_dir.mkdir(parents=True, exist_ok=True)
    lockfile = (settings.agent_dir / 'agent.lock').open('w')
    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    ocr = Path(settings.scoreboard_ocr_bin)
    source = Path(__file__).resolve().parents[2] / 'scripts' / 'scoreboard_ocr.swift'
    if sys.platform == 'darwin' and settings.scoreboard_ocr_bin == 'data/tools/scoreboard-ocr' and (
            not ocr.exists() or source.stat().st_mtime > ocr.stat().st_mtime):
        ocr.parent.mkdir(parents=True, exist_ok=True)
        await run('swiftc', str(source), '-o', str(ocr), timeout=60)
    sessions = {}
    reddit = BrowserRedditClient() if settings.reddit_source == 'browser' else RedditClient()
    judge = SocialJudge(reddit.client)
    hype_judge = HypeJudge()
    last_cleanup = 0
    previous_games = {g['game']['game_id'] for g in load_json(settings.agent_dir / 'status.json', {}).get('games', [])}
    last_discovery = 0
    discovery_error = None
    available = []
    stopping = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, stopping.set)
    try:
        while not stopping.is_set():
            enabled = load_json(settings.agent_dir / 'control.json', {'enabled': True}).get('enabled', True)
            if enabled and not settings.demo_mode and time.time() - last_discovery >= 60:
                try:
                    report = await discover()
                    # MLB uses the official highlight collector until its video alignment
                    # is enabled; see DISCOVERY_EXCLUDED_LEAGUES at the top of this module.
                    available = [g for g in report['games'] if g['status'] == 'matched'
                                 and g['league'] not in DISCOVERY_EXCLUDED_LEAGUES]
                    discovery_error = '; '.join(w.get('error', '') for w in report.get('warnings', [])) or None
                    # Keep existing games stable, then prefer college football and recently started games.
                    available.sort(key=lambda g: g.get('starts_at', ''), reverse=True)
                    available.sort(key=lambda g: g['league'] != 'ncaaf')
                    if not sessions:
                        available.sort(key=lambda g: g['game_id'] not in previous_games)
                except Exception as error:
                    discovery_error = type(error).__name__
                last_discovery = time.time()
                # Finished/inactive games must not accumulate footage indefinitely.
                active_dirs = {monitor.directory for monitor, _ in sessions.values()}
                for index in settings.agent_dir.glob('*-*/index.json'):
                    if index.parent in active_dirs:
                        continue
                    segments = load_json(index, [])
                    keep = []
                    for segment in segments:
                        if segment['start'] + segment['duration'] < time.time() - settings.agent_buffer_minutes * 60:
                            if Path(segment['file']).suffix == '.ts':  # raw buffer only, never clips
                                (index.parent / Path(segment['file']).name).unlink(missing_ok=True)
                        else:
                            keep.append(segment)
                    atomic_json(index, keep)
            for key, (monitor, task) in list(sessions.items()):
                if not enabled or task.done() or (monitor.finished_at and time.time() - monitor.finished_at > 660):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    del sessions[key]
            if enabled and not settings.demo_mode:
                for game in available:
                    key = f"{game['league']}:{game['game_id']}"
                    if key not in sessions and len(sessions) < settings.agent_max_games:
                        monitor = GameMonitor(game, reddit, judge, hype_judge)
                        sessions[key] = (monitor, asyncio.create_task(monitor.run()))
            if time.time() - last_cleanup >= PENDING_CLEANUP_SECONDS:
                last_cleanup = time.time()
                try:  # rejected held clips only; published clips are never touched
                    removed = await asyncio.to_thread(cleanup_pending)
                    if removed:
                        log.info('Removed %d expired rejected held clip file(s)', len(removed))
                except Exception as error:
                    log.warning('Held clip cleanup failed: %s', type(error).__name__)
            atomic_json(settings.agent_dir / 'status.json', {'enabled': enabled, 'heartbeat': iso(time.time()),
                'last_discovery': iso(last_discovery), 'matched_games': len(available),
                'max_games': settings.agent_max_games, 'error': discovery_error, 'reddit': configuration_status(),
                'games': [monitor.status() for monitor, _ in sessions.values()]})
            try:
                await asyncio.wait_for(stopping.wait(), 2)
            except TimeoutError:
                pass
    finally:
        for _, task in sessions.values():
            task.cancel()
        await asyncio.gather(*(task for _, task in sessions.values()), return_exceptions=True)
        await reddit.close()
        await hype_judge.close()
        lockfile.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    asyncio.run(main())
