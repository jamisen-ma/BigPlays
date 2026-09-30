"""Read-only public reactions with source timestamps and explicit access states.

Mastodon works without credentials where public previews are enabled. X is never
queried unless both a Bearer Token and explicit paid-request opt-in are supplied.
These feeds are samples of discussion, not evidence that a post concerns a clip.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

MASTODON = 'https://mastodon.social'
HASHTAGS = {'nfl': 'NFL', 'mlb': 'MLB', 'nba': 'NBA', 'ncaaf': 'CFB'}


def utc(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError('Timestamps must include a timezone')
    return result.astimezone(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag in ('p', 'br', 'div', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ('p', 'div', 'li'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(content: str) -> str:
    parser = PlainText()
    parser.feed(content)
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def public_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def author_key(provider: str, value: str) -> str:
    return hashlib.sha256(f'{provider}:{value}'.encode()).hexdigest()


def metrics(values: dict, names: dict) -> dict:
    return {target: values[source] for source, target in names.items()
            if isinstance(values.get(source), int) and not isinstance(values[source], bool) and values[source] >= 0}


def result(provider: str, status: str = 'ok', **kwargs) -> dict:
    return {'provider': provider, 'status': status, 'posts': [], 'checked_at': iso(),
            'error': None, 'retry_after_seconds': None, 'next_cursor': None, **kwargs}


def retry_after(response: httpx.Response, now: float | None = None) -> int:
    now = time.time() if now is None else now
    value = response.headers.get('retry-after')
    try:
        if value:
            return max(1, int(float(value)))
        reset = response.headers.get('x-rate-limit-reset') or response.headers.get('x-ratelimit-reset')
        if reset:
            try:
                return max(1, int(float(reset) - now) + 1)
            except ValueError:
                return max(1, int(parsedate_to_datetime(reset).timestamp() - now) + 1)
    except (ValueError, TypeError, OverflowError):
        pass
    if value:
        try:
            return max(1, int(parsedate_to_datetime(value).timestamp() - now) + 1)
        except (ValueError, TypeError, OverflowError):
            pass
    return 60


def failure(provider: str, response: httpx.Response) -> dict:
    code = response.status_code
    status = {401: 'needs_credentials', 402: 'credits_required', 403: 'access_denied',
              429: 'rate_limited'}.get(code, 'error')
    return result(provider, status, http_status=code,
                  error=f'{provider.title()} returned HTTP {code}.',
                  retry_after_seconds=retry_after(response) if code == 429 else None)


def normalize_mastodon(row: dict, collected_at: str) -> dict | None:
    account = row.get('account') or {}
    if (row.get('visibility') != 'public' or row.get('reblog') or account.get('bot')
            or row.get('sensitive') or row.get('spoiler_text')):
        return None
    post_id = str(row.get('id', ''))
    url = public_url(row.get('url'))
    if not post_id.isdigit() or not url or not account.get('acct'):
        return None
    try:
        created_at = iso(utc(row['created_at']))
        text = plain_text(row['content'])
    except (KeyError, ValueError, TypeError):
        return None
    if not text:
        return None
    return {'id': f'mastodon:{post_id}', 'provider': 'mastodon', 'text': text,
            'created_at': created_at, 'collected_at': collected_at, 'url': url,
            'author_display_name': account.get('display_name') or account['acct'],
            'author_key': author_key('mastodon', account.get('url') or account['acct']),
            'metrics': metrics(row, {'favourites_count': 'likes', 'reblogs_count': 'reposts',
                                     'replies_count': 'replies'})}


async def fetch_public_feed(league: str, *, client: httpx.AsyncClient | None = None) -> dict:
    """One page of current hashtag discussion; callers may cache briefly in RAM."""
    if league not in HASHTAGS:
        raise ValueError('Unsupported sports league')
    tag = HASHTAGS[league]
    coverage = f'Latest #{tag} posts visible to mastodon.social; a sample, not all social discussion.'
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)
    try:
        response = await client.get(f'{MASTODON}/api/v1/timelines/tag/{tag}', params={'limit': 40},
                                    headers={'User-Agent': 'BigPlays/0.1 (public sports discussion)'})
        if response.status_code != 200:
            return failure('mastodon', response) | {'coverage': coverage}
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError('Invalid timeline response')
        collected_at = iso()
        posts = {}
        for row in payload[:40]:
            if isinstance(row, dict) and (post := normalize_mastodon(row, collected_at)):
                posts[post['id']] = post
        return result('mastodon', 'ok' if posts else 'empty', checked_at=collected_at,
                      posts=sorted(posts.values(), key=lambda p: p['created_at'], reverse=True),
                      coverage=coverage, source_url=f'{MASTODON}/tags/{tag}',
                      received_count=len(payload), excluded_count=len(payload) - len(posts))
    except (httpx.HTTPError, ValueError, TypeError):
        return result('mastodon', 'error', error='Public discussion is temporarily unavailable.', coverage=coverage)
    finally:
        if own_client:
            await client.aclose()


def normalize_x(row: dict, collected_at: str) -> dict | None:
    post_id, author_id = str(row.get('id', '')), str(row.get('author_id', ''))
    if not post_id.isdigit() or not author_id.isdigit() or not row.get('text'):
        return None
    if any(ref.get('type') == 'retweeted' for ref in row.get('referenced_tweets', [])):
        return None
    try:
        created_at = iso(utc(row['created_at']))
    except (KeyError, ValueError, TypeError):
        return None
    return {'id': f'x:{post_id}', 'provider': 'x', 'text': row['text'], 'created_at': created_at,
            'collected_at': collected_at, 'url': f'https://x.com/i/web/status/{post_id}',
            'author_display_name': None, 'author_key': author_key('x', author_id),
            'conversation_id': row.get('conversation_id'),
            'metrics': metrics(row.get('public_metrics') or {}, {'like_count': 'likes',
                'retweet_count': 'reposts', 'reply_count': 'replies', 'quote_count': 'quotes',
                'impression_count': 'impressions'})}


class XRecentSearchClient:
    """One bounded official API request per explicit search; never auto-paginates."""

    def __init__(self, bearer_token: str | None = None, *, paid_enabled: bool = False,
                 client: httpx.AsyncClient | None = None):
        self._bearer_token = bearer_token
        self.paid_enabled = paid_enabled
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        self._own_client = client is None
        self._retry_at = 0
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls):
        return cls(os.getenv('X_BEARER_TOKEN'), paid_enabled=os.getenv('X_PAID_REQUESTS_ENABLED', '').lower() == 'true')

    def configuration_status(self) -> dict:
        if not self._bearer_token:
            return result('x', 'needs_credentials', missing=['X_BEARER_TOKEN'])
        if not self.paid_enabled:
            return result('x', 'disabled', error='Paid X requests have not been enabled.')
        return result('x', 'ready')

    async def close(self):
        if self._own_client:
            await self.client.aclose()

    async def search(self, query: str, start: str, end: str, *, max_results: int = 10,
                     next_cursor: str | None = None) -> dict:
        configured = self.configuration_status()
        if configured['status'] != 'ready':
            return configured
        start_at, end_at = utc(start), utc(end)
        now = datetime.now(timezone.utc)
        if not query.strip() or len(query) > 512:
            raise ValueError('X query must contain 1–512 characters')
        if not now - timedelta(days=7) <= start_at < end_at <= now:
            raise ValueError('Recent search needs an ordered time window within the past seven days')
        if not 10 <= max_results <= 100:
            raise ValueError('X max_results must be between 10 and 100')
        params = {'query': query, 'start_time': iso(start_at), 'end_time': iso(end_at),
                  'max_results': max_results, 'sort_order': 'recency',
                  'tweet.fields': 'created_at,author_id,public_metrics,conversation_id,referenced_tweets'}
        if next_cursor:
            params['next_token'] = next_cursor
        async with self._lock:
            if self._retry_at > time.monotonic():
                return result('x', 'rate_limited', retry_after_seconds=int(self._retry_at - time.monotonic()) + 1)
            try:
                response = await self.client.get('https://api.x.com/2/tweets/search/recent', params=params,
                    headers={'Authorization': f'Bearer {self._bearer_token}'})
                if response.status_code != 200:
                    failed = failure('x', response)
                    if response.status_code == 429:
                        self._retry_at = time.monotonic() + failed['retry_after_seconds']
                    return failed
                remaining = response.headers.get('x-rate-limit-remaining')
                if remaining == '0':
                    self._retry_at = time.monotonic() + retry_after(response)
                payload = response.json()
                rows = payload.get('data', [])
                if not isinstance(rows, list):
                    raise ValueError('Invalid X response')
                collected_at = iso()
                posts = {}
                for row in rows:
                    if isinstance(row, dict) and (post := normalize_x(row, collected_at)):
                        if start_at <= utc(post['created_at']) < end_at:
                            posts[post['id']] = post
                cursor = payload.get('meta', {}).get('next_token')
                return result('x', 'partial' if payload.get('errors') else 'ok' if posts else 'empty',
                    checked_at=collected_at, posts=list(posts.values()), next_cursor=cursor,
                    received_count=len(rows), excluded_count=len(rows) - len(posts),
                    coverage='One page of matching public X posts; current engagement counts, not historical counts.')
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                return result('x', 'error', error='X search is temporarily unavailable.')


def main():
    import argparse
    from dotenv import load_dotenv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['mastodon', 'x'], default='mastodon')
    parser.add_argument('--league', choices=sorted(HASHTAGS), default='nfl')
    parser.add_argument('--query')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--max-results', type=int, default=10)
    args = parser.parse_args()
    load_dotenv()

    async def run():
        if args.provider == 'mastodon':
            return await fetch_public_feed(args.league)
        client = XRecentSearchClient.from_env()
        try:
            if client.configuration_status()['status'] != 'ready':
                return client.configuration_status()
            if not all([args.query, args.start, args.end]):
                parser.error('X requires --query, --start and --end')
            return await client.search(args.query, args.start, args.end, max_results=args.max_results)
        finally:
            await client.close()
    print(json.dumps(asyncio.run(run()), indent=2))


if __name__ == '__main__':
    main()
