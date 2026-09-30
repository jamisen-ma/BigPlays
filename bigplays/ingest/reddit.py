"""Read-only authenticated game-thread collection. No simulated comments."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time

import httpx

from bigplays.config import gate_mode, settings
from bigplays.ingest.live_streams import words
from bigplays.media.timeline import timestamp

SUBREDDITS = {'ncaaf': 'CFB', 'nfl': 'nfl', 'nba': 'nba', 'mlb': 'baseball'}


def configuration_status():
    missing = [name for name, value in [('REDDIT_CLIENT_ID', settings.reddit_client_id),
        ('REDDIT_CLIENT_SECRET', settings.reddit_client_secret)] if not value]
    if settings.reddit_source == 'browser':
        missing = []
    if settings.social_llm_provider == 'anthropic' and not settings.anthropic_api_key:
        missing.append('ANTHROPIC_API_KEY')
    return {'status': 'needs_credentials' if missing else 'ready' if settings.reddit_enabled and settings.use_llm else 'disabled',
            'missing': missing, 'reddit_enabled': settings.reddit_enabled, 'llm_enabled': settings.use_llm,
            'llm_provider': settings.social_llm_provider, 'llm_model': settings.social_llm_model,
            'source': settings.reddit_source, 'clip_gate': gate_mode() != 'off', 'clip_gate_mode': gate_mode()}


def matching_threads(posts, game, aliases):
    kickoff = timestamp(game['starts_at'])
    matches = []
    for post in posts:
        title = words(post.get('title', ''))
        if (' game thread ' not in title or ' post game ' in title or ' postgame ' in title
                or post.get('over_18') or not re.fullmatch(r'[a-z0-9]+', post.get('id', ''))):
            continue
        if not kickoff - 12 * 3600 <= post.get('created_utc', 0) <= kickoff + 6 * 3600:
            continue
        if len(aliases) == 2 and all(any(words(alias) in title for alias in team if len(alias) >= 3) for team in aliases):
            matches.append(post)
    return sorted(matches, key=lambda p: p['created_utc'], reverse=True)


def parse_comments(payload, thread_id):
    pending = list(payload[1].get('data', {}).get('children', [])) if isinstance(payload, list) and len(payload) > 1 else []
    comments = {}
    while pending:
        item = pending.pop()
        if item.get('kind') != 't1':
            continue
        data = item.get('data', {})
        replies = data.get('replies')
        if isinstance(replies, dict):
            pending.extend(replies.get('data', {}).get('children', []))
        body, author, cid = data.get('body', ''), data.get('author'), data.get('id', '')
        if not re.fullmatch(r'[a-z0-9]+', cid) or data.get('link_id') != 't3_' + thread_id:
            continue
        if author == '[deleted]' or body in ('[deleted]', '[removed]'):
            comments[cid] = {'id': cid, 'deleted': True}
            continue
        if not author or author == 'AutoModerator':
            continue
        comments[cid] = {'id': cid, 'body': body[:800], 'created': float(data['created_utc']),
            'author': hashlib.sha256(author.lower().encode()).hexdigest(),
            'url': f'https://www.reddit.com/comments/{thread_id}/_/{cid}/'}
    return list(comments.values())


class RedditClient:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
        self.lock = asyncio.Lock()
        self.token = None
        self.expires = 0
        self.next_request = 0
        self.search_cache = {}

    async def close(self):
        await self.client.aclose()

    async def get(self, path, params):
        if not settings.reddit_enabled or not settings.reddit_client_id or not settings.reddit_client_secret:
            raise ValueError('Reddit access is not configured')
        async with self.lock:
            if self.next_request > time.monotonic():
                raise ValueError('Reddit rate limit backoff')
            self.next_request = time.monotonic() + 1
            if not self.token or time.time() >= self.expires:
                grant = {'grant_type': 'refresh_token', 'refresh_token': settings.reddit_refresh_token} if settings.reddit_refresh_token else {'grant_type': 'client_credentials'}
                response = await self.client.post('https://www.reddit.com/api/v1/access_token', data=grant,
                    auth=(settings.reddit_client_id, settings.reddit_client_secret),
                    headers={'User-Agent': settings.reddit_user_agent})
                response.raise_for_status()
                data = response.json()
                if not data.get('access_token'):
                    raise ValueError('Reddit OAuth did not issue a token')
                self.token = data['access_token']
                self.expires = time.time() + max(1, data.get('expires_in', 3600) - 60)
            response = await self.client.get('https://oauth.reddit.com' + path, params=params,
                headers={'Authorization': 'Bearer ' + self.token, 'User-Agent': settings.reddit_user_agent})
            if response.status_code == 401:
                self.token = None
            if response.status_code == 429 or float(response.headers.get('x-ratelimit-remaining', '100')) < 2:
                wait = float(response.headers.get('retry-after', response.headers.get('x-ratelimit-reset', '60')))
                self.next_request = time.monotonic() + max(1, min(wait, 3600))
            response.raise_for_status()
            return response.json()

    async def find_thread(self, game, aliases):
        subreddit = SUBREDDITS[game['league']]
        cached = self.search_cache.get(subreddit)
        if cached and time.time() - cached[0] < 300:
            posts = cached[1]
        else:
            result = await self.get(f'/r/{subreddit}/search', {'q': '"game thread"', 'restrict_sr': 'true',
                'sort': 'new', 't': 'day', 'limit': 100, 'raw_json': 1})
            posts = [item['data'] for item in result.get('data', {}).get('children', []) if item.get('kind') == 't3']
            self.search_cache[subreddit] = (time.time(), posts)
        matches = matching_threads(posts, game, aliases)
        return matches[0] if matches else None

    async def comments(self, thread_id):
        if not re.fullmatch(r'[a-z0-9]+', thread_id):
            raise ValueError('Invalid Reddit thread ID')
        result = await self.get(f'/comments/{thread_id}', {'sort': 'new', 'limit': 100, 'depth': 2, 'raw_json': 1})
        return parse_comments(result, thread_id)
