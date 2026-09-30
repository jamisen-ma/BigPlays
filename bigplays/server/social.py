"""Public social discussion API: cached feeds, provider status, play relevance.

Nothing here is on the clipping path. Feeds are fetched on demand (at most one
Mastodon request per league per ``CACHE_TTL_SECONDS``), Reddit game-thread comments
come from the rate-limited RSS poller (``bigplays.ingest.reddit_rss``) started by
this router's lifespan, and clip enrichment runs as background tasks after a clip's
sidecar has been published.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Query

from bigplays.config import gate_mode, settings
from bigplays.ingest import reddit_rss, social_feeds
from bigplays.ingest.reddit import configuration_status as reddit_configuration
from bigplays.orchestrator.social_ranker import (PRE_EVENT_TOLERANCE_SECONDS, REACTION_WINDOW_SECONDS,
                                                 SocialEnricher, _seconds, any_provider_ok, classify_posts,
                                                 sidecar_for)
from bigplays.server.events import bus
from bigplays.server.streams import failure
from bigplays.storage.catalog import catalog_for

CACHE_TTL_SECONDS = 60
LEAGUES = ('nfl', 'mlb')


class FeedCache:
    """Per-league in-memory cache. Concurrent requests share one upstream fetch."""

    def __init__(self, fetch=None, ttl: int = CACHE_TTL_SECONDS, clock=time.monotonic):
        self.fetch = fetch or social_feeds.fetch_public_feed
        self.ttl, self.clock = ttl, clock
        self.entries: dict[str, dict] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    def peek(self, league: str) -> dict | None:
        return self.entries.get(league)

    async def get(self, league: str) -> tuple[dict, bool]:
        """Return (entry, served_from_cache). Entry keys: result, at, expires, last_good."""
        entry = self.entries.get(league)
        if entry and self.clock() < entry['expires']:
            return entry, True
        lock = self.locks.setdefault(league, asyncio.Lock())
        async with lock:
            entry = self.entries.get(league)
            if entry and self.clock() < entry['expires']:
                return entry, True
            try:
                result = await self.fetch(league)
            except Exception:
                result = social_feeds.result('mastodon', 'error', error='Public discussion is temporarily unavailable.')
            now = self.clock()
            ttl = max(self.ttl, result.get('retry_after_seconds') or 0)
            good = result.get('status') in ('ok', 'empty')
            entry = {'result': result, 'at': now, 'expires': now + ttl,
                     'last_good': result if good else (entry or {}).get('last_good')}
            self.entries[league] = entry
            return entry, False


feed_cache = FeedCache()
_background: set[asyncio.Task] = set()


def _background_task(coro):
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


# --------------------------------------------------------------------------- status

def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        try:
            from dotenv import dotenv_values
            value = dotenv_values(Path('.env')).get(name)
        except Exception:
            value = None
    return value or None


def mastodon_status(result: dict | None, *, stale: bool = False) -> dict:
    if result is None:
        return {'status': 'not_checked', 'fetched_at': None, 'detail': None, 'error': None,
                'retry_after_seconds': None, 'used_in_feed': True}
    ok = result.get('status') in ('ok', 'empty')
    return {'status': 'ok' if ok else 'error', 'fetched_at': result.get('checked_at'),
            'detail': result.get('status'), 'error': result.get('error'),
            'retry_after_seconds': result.get('retry_after_seconds'),
            'source_url': result.get('source_url'), 'coverage': result.get('coverage'),
            'serving_stale_posts': stale, 'used_in_feed': True}


def x_status() -> dict:
    token, paid = _env('X_BEARER_TOKEN'), (_env('X_PAID_REQUESTS_ENABLED') or '').lower() == 'true'
    status = 'needs_token' if not token else 'disabled_paid' if not paid else 'ready'
    return {'status': status, 'missing': [] if token else ['X_BEARER_TOKEN'], 'paid_requests_enabled': paid,
            'used_in_feed': False,
            'note': 'Paid X recent search is never called by this API; it is manual and opt-in only.'}


def reddit_judge_status() -> dict:
    """The optional OAuth/LLM game-thread judge used by the capture agent's social gate."""
    config = reddit_configuration()
    missing = [name for name, value in [('REDDIT_CLIENT_ID', settings.reddit_client_id),
                                        ('REDDIT_CLIENT_SECRET', settings.reddit_client_secret)] if not value]
    status = 'missing_credentials' if missing else 'configured' if settings.reddit_enabled else 'disabled'
    return {'status': status, 'missing': missing, 'source': config.get('source'),
            'reddit_enabled': settings.reddit_enabled,
            'note': 'Reddit OAuth comments feed the game-thread reaction judge only; browser verification is never bypassed.'}


def reddit_status(league: str | None = None) -> dict:
    """Public RSS game-thread comments: ok / rate_limited / error / no_live_threads (or not_checked / disabled)."""
    return {**reddit_rss.poller.status(league), 'judge': reddit_judge_status()}


def providers(league: str | None = None) -> dict:
    if league:
        entry = feed_cache.peek(league)
        result = entry['result'] if entry else None
        stale = bool(entry and result.get('status') not in ('ok', 'empty') and entry.get('last_good'))
        mastodon = mastodon_status(result, stale=stale)
    else:
        leagues = {name: mastodon_status((feed_cache.peek(name) or {}).get('result')) for name in LEAGUES}
        checked = [s for s in leagues.values() if s['fetched_at']]
        latest = max(checked, key=lambda s: s['fetched_at']) if checked else mastodon_status(None)
        mastodon = {**latest, 'leagues': leagues}
    return {'mastodon': mastodon, 'x': x_status(), 'reddit': reddit_status(league)}


# --------------------------------------------------------------------------- feed

def dedupe(posts: list[dict]) -> list[dict]:
    """Drop repeated IDs and the same author posting the same text twice."""
    seen_ids, seen_text, unique = set(), set(), []
    for post in sorted(posts, key=lambda p: p.get('created_at') or '', reverse=True):
        text_key = (post.get('author_key'), re.sub(r'\W+', ' ', (post.get('text') or '').lower()).strip())
        if post.get('id') in seen_ids or text_key in seen_text:
            continue
        seen_ids.add(post.get('id'))
        seen_text.add(text_key)
        unique.append(post)
    return unique


def candidate_clips(league: str, posts: list[dict]) -> list[dict]:
    """Published clips whose reaction window overlaps the sampled posts."""
    times = [t for p in posts if (t := _seconds(p.get('created_at'))) is not None]
    if not times:
        return []
    earliest, latest = min(times) - REACTION_WINDOW_SECONDS, max(times) + PRE_EVENT_TOLERANCE_SECONDS
    try:
        records = catalog_for(settings.clips_dir, settings.database_path).all()
    except Exception:
        return []
    return [r for r in records if str(r.get('league') or '').lower() == league
            and (t := _seconds(r.get('occurred_utc'))) is not None and earliest <= t <= latest]


async def cached_feed(league: str) -> dict:
    """Deduplicated, classified posts for ``league`` (shared by API and enricher)."""
    entry, cached = await feed_cache.get(league)
    result = entry['result']
    stale = result.get('status') not in ('ok', 'empty') and bool(entry.get('last_good'))
    source = entry['last_good'] if stale else result
    posts = dedupe(source.get('posts', []) + reddit_rss.poller.posts(league))
    clips = await asyncio.to_thread(candidate_clips, league, posts)
    posts = classify_posts(posts, clips)
    return {'league': league, 'posts': posts, 'clips': clips, 'cached': cached, 'stale': stale,
            'fetched_at': source.get('checked_at'), 'age_seconds': round(feed_cache.clock() - entry['at'], 1),
            'coverage': result.get('coverage') or source.get('coverage'),
            'providers': providers(league)}


enricher = SocialEnricher(cached_feed)


def _enrich_matched(feed: dict):
    """After a fresh fetch, enrich already-published clips that gained evidence."""
    matched = {m['clip_id'] for p in feed['posts'] for m in p['play_matches']}
    for clip in feed['clips']:
        if clip.get('event_id') in matched:
            _background_task(enricher.enrich_once(clip, feed=feed))


async def _enrich_from_reddit(league: str, new_posts: list[dict]):
    """New game-thread comments arrived: enrich published clips they are evidence for."""
    clips = await asyncio.to_thread(candidate_clips, league, new_posts)
    if clips:
        _enrich_matched({'posts': classify_posts(new_posts, clips), 'clips': clips, 'providers': providers(league)})


def _on_reddit_posts(league: str, game_id: str, new_posts: list[dict]):
    if league in LEAGUES:
        _background_task(_enrich_from_reddit(league, new_posts))


async def _listen_for_new_clips():
    """Schedule enrichment when the clips watcher announces a newly published clip."""
    queue = bus.subscribe()
    try:
        while True:
            event = await queue.get()
            record = event.data if event.type == 'highlight' else None
            if not record or str(record.get('league') or '').lower() not in LEAGUES:
                continue
            occurred = _seconds(record.get('occurred_utc'))
            if occurred is not None and time.time() - occurred <= REACTION_WINDOW_SECONDS:
                enricher.schedule(record)  # returns immediately; never delays publication
    finally:
        bus.unsubscribe(queue)


@asynccontextmanager
async def social_lifespan(app):
    loop = asyncio.get_running_loop()
    task = loop.create_task(_listen_for_new_clips())
    reddit_task = None
    if settings.reddit_rss_enabled:  # read-only RSS poller; one shared, header-honoring request queue
        reddit_rss.poller.listeners.append(_on_reddit_posts)
        reddit_task = loop.create_task(reddit_rss.poller.run())
    try:
        yield
    finally:
        task.cancel()
        if reddit_task:
            reddit_task.cancel()
            reddit_rss.poller.listeners.remove(_on_reddit_posts)
            await reddit_rss.poller.reader.close()
        for pending in list(enricher.tasks.values()) + list(_background):
            pending.cancel()


router = APIRouter(lifespan=social_lifespan)


def _post_view(post: dict) -> dict:
    return {key: post.get(key) for key in ('id', 'provider', 'text', 'created_at', 'collected_at', 'url',
                                           'author_display_name', 'author_key', 'metrics',
                                           'relevance', 'play_matches')}


@router.get('/api/social/feed')
async def social_feed(league: str = Query('nfl', pattern='^(nfl|mlb)$'), limit: int = Query(30, ge=1, le=100)):
    feed = await cached_feed(league)
    if not feed['cached'] and any_provider_ok(feed['providers']):
        _enrich_matched(feed)
    posts = [_post_view(p) for p in feed['posts'][:limit]]
    return {'ok': True, 'league': league, 'fetched_at': feed['fetched_at'], 'cached': feed['cached'],
            'stale': feed['stale'], 'cache_age_seconds': feed['age_seconds'],
            'cache_ttl_seconds': feed_cache.ttl, 'count': len(posts),
            'play_evidence_count': sum(p['relevance'] == 'play_evidence' for p in posts),
            'coverage': feed['coverage'], 'posts': posts, 'providers': feed['providers']}


@router.get('/api/social/status')
def social_status():
    return {'ok': True, 'providers': providers(), 'cache_ttl_seconds': feed_cache.ttl,
            'clip_gate': gate_mode() != 'off', 'clip_gate_mode': gate_mode()}


def find_clip(clip_id: str) -> dict | None:
    try:
        for record in catalog_for(settings.clips_dir, settings.database_path).all():
            if record.get('event_id') == clip_id:
                return record
    except Exception:
        pass
    path = sidecar_for({'event_id': clip_id})
    if path:
        try:
            record = json.loads(path.read_text())
            return record if isinstance(record, dict) and record.get('event_id') == clip_id else None
        except (OSError, ValueError):
            return None
    return None


@router.get('/api/social/play/{clip_id}')
async def social_for_play(clip_id: str, limit: int = Query(20, ge=1, le=100)):
    if not re.fullmatch(r'[\w.\-]{1,100}', clip_id):
        return failure('social', 'Invalid clip id', 400)
    record = await asyncio.to_thread(find_clip, clip_id)
    if not record:
        return failure('social', 'Clip not found', 404)
    league = str(record.get('league') or '').lower()
    clip = {'clip_id': clip_id, 'league': league, 'title': record.get('title'),
            'occurred_utc': record.get('occurred_utc'), 'social_score': record.get('social_score'),
            'social_enrichment': record.get('social_enrichment')}
    if league not in LEAGUES:
        return {'ok': True, 'clip': clip, 'count': 0, 'posts': [],
                'providers': {'mastodon': {'status': 'unsupported_league', 'used_in_feed': True},
                              'x': x_status(), 'reddit': reddit_status()}}
    feed = await cached_feed(league)
    matched = [p for p in classify_posts(feed['posts'], [record]) if p['relevance'] == 'play_evidence']
    if matched and any_provider_ok(feed['providers']):
        _background_task(enricher.enrich_once(record, feed=feed))
    return {'ok': True, 'clip': clip, 'fetched_at': feed['fetched_at'], 'cached': feed['cached'],
            'stale': feed['stale'], 'count': len(matched[:limit]),
            'window': {'before_seconds': PRE_EVENT_TOLERANCE_SECONDS, 'after_seconds': REACTION_WINDOW_SECONDS},
            'posts': [_post_view(p) for p in matched[:limit]], 'providers': feed['providers']}
