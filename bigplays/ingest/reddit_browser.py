"""Bounded public-browser reads; blocks back off and never become approvals."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from bigplays.ingest.reddit import RedditClient, SUBREDDITS, matching_threads, parse_comments


class BrowserReadUnavailable(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


class BrowserRedditClient(RedditClient):
    def __init__(self):
        super().__init__()
        self.browser_lock = asyncio.Lock()
        self.browser_retry_at = 0
        self.browser_status = 'browser_unavailable'

    async def read(self, mode, value):
        async with self.browser_lock:
            if time.monotonic() < self.browser_retry_at:
                raise BrowserReadUnavailable(self.browser_status)
            script = Path(__file__).resolve().parents[2] / 'frontend/scripts/reddit-reader.mjs'
            process = await asyncio.create_subprocess_exec('node', str(script), mode, value,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), 35)
                result = json.loads(stdout)
            except BaseException:
                if process.returncode is None:
                    process.terminate()  # Allow the helper to close Chrome before killing it.
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                self.browser_retry_at = time.monotonic() + 300
                raise
            if not result.get('ok'):
                allowed = {'browser_access_blocked', 'browser_unavailable', 'browser_no_timestamped_content'}
                self.browser_status = result.get('status') if result.get('status') in allowed else 'browser_unavailable'
                self.browser_retry_at = time.monotonic() + 300
                raise BrowserReadUnavailable(self.browser_status)
            return result['items']

    async def find_thread(self, game, aliases):
        subreddit = SUBREDDITS[game['league']]
        cached = self.search_cache.get(subreddit)
        if cached and time.time() - cached[0] < 300:
            posts = cached[1]
        else:
            posts = await self.read('threads', subreddit)
            self.search_cache[subreddit] = (time.time(), posts)
        matches = matching_threads(posts, game, aliases)
        return matches[0] if matches else None

    async def comments(self, thread_id):
        rows = await self.read('comments', thread_id)
        return parse_comments([{}, {'data': {'children': [{'kind': 't1', 'data': row} for row in rows]}}], thread_id)
