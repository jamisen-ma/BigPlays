"""Live game-thread reactions from Reddit's official public RSS/Atom feeds.

Reddit publishes an Atom feed for every subreddit listing and comment page
(``/r/<sub>/new/.rss``, ``<thread>/.rss?sort=new``). This module reads only those
feeds, with an honest identifying User-Agent, through ONE process-wide queue that
honors Reddit's ``x-ratelimit-*`` and ``Retry-After`` headers, spaces requests at
least ``REDDIT_RSS_MIN_INTERVAL_SECONDS`` apart and backs off exponentially on 429s
or errors. It never uses the ``.json`` endpoints, browser automation, proxies or
anything else that works around Reddit's controls.

Flow (``RedditRSSPoller``): ESPN scoreboard -> live NFL/MLB games -> each team's
subreddit plus r/nfl / r/baseball ``new`` listing -> "Game Thread" / "Post Game
Thread" posts matching both teams and the date -> round-robin polls of each
thread's newest ~100 comments -> normalized ``Post`` dicts (the shape used by
``bigplays.ingest.social_feeds``) kept per game in memory and in a small JSON file.
Nothing here is on the clipping path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from bigplays.config import settings
from bigplays.ingest.social_feeds import author_key, iso, plain_text, utc

log = logging.getLogger(__name__)

BASE = 'https://www.reddit.com'
ATOM = '{http://www.w3.org/2005/Atom}'
DEFAULT_USER_AGENT = 'BigPlays/0.1 (personal sports reactions reader)'
LEAGUES = ('nfl', 'mlb')
LEAGUE_SUBREDDITS = {'mlb': 'baseball', 'nfl': 'nfl'}

# ESPN team abbreviation -> team subreddit. Reddit subreddit names are
# case-insensitive; alternate ESPN/MLB codes share an entry.
TEAM_SUBREDDITS = {
    'mlb': {
        'ARI': 'azdiamondbacks', 'AZ': 'azdiamondbacks', 'ATH': 'OaklandAthletics', 'OAK': 'OaklandAthletics',
        'ATL': 'Braves', 'BAL': 'orioles', 'BOS': 'redsox', 'CHC': 'CHICubs', 'CHW': 'whitesox',
        'CWS': 'whitesox', 'CIN': 'Reds', 'CLE': 'ClevelandGuardians', 'COL': 'ColoradoRockies',
        'DET': 'motorcitykitties', 'HOU': 'Astros', 'KC': 'KCRoyals', 'LAA': 'angelsbaseball',
        'LAD': 'Dodgers', 'MIA': 'letsgofish', 'MIL': 'Brewers', 'MIN': 'minnesotatwins',
        'NYM': 'NewYorkMets', 'NYY': 'NYYankees', 'PHI': 'phillies', 'PIT': 'buccos', 'SD': 'Padres',
        'SF': 'SFGiants', 'SEA': 'Mariners', 'STL': 'Cardinals', 'TB': 'tampabayrays',
        'TEX': 'TexasRangers', 'TOR': 'Torontobluejays', 'WSH': 'Nationals', 'WSN': 'Nationals',
    },
    'nfl': {
        'ARI': 'AZCardinals', 'ATL': 'falcons', 'BAL': 'ravens', 'BUF': 'buffalobills', 'CAR': 'panthers',
        'CHI': 'CHIBears', 'CIN': 'bengals', 'CLE': 'Browns', 'DAL': 'cowboys', 'DEN': 'DenverBroncos',
        'DET': 'detroitlions', 'GB': 'GreenBayPackers', 'HOU': 'Texans', 'IND': 'Colts', 'JAX': 'Jaguars',
        'KC': 'KansasCityChiefs', 'LV': 'raiders', 'LAC': 'Chargers', 'LAR': 'LosAngelesRams',
        'MIA': 'miamidolphins', 'MIN': 'minnesotavikings', 'NE': 'Patriots', 'NO': 'Saints',
        'NYG': 'NYGiants', 'NYJ': 'nyjets', 'PHI': 'eagles', 'PIT': 'steelers', 'SF': '49ers',
        'SEA': 'Seahawks', 'TB': 'buccaneers', 'TEN': 'Tennesseetitans', 'WSH': 'Commanders',
        'WAS': 'Commanders',
    },
}

REDISCOVER_SECONDS = 600          # a subreddit listing is re-read at most every 10 min
POST_GAME_GRACE_SECONDS = 1800    # keep following a finished game for post-game threads
STALE_LIVE_SECONDS = 1800         # a "live" game the scoreboard stopped confirming is dropped
THREADS_PER_GAME = 2              # home + away team threads; the league thread fills a missing slot
SCOREBOARD_SECONDS = 60
MAX_POSTS_PER_GAME = 1500
POST_RETENTION_SECONDS = 12 * 3600
MAX_TEXT = 1000
POLL_LOG = 180                    # per-thread poll history kept for coverage (about 2-3 h)
PRIORITY_FILE = 'reddit_rss_priority.json'
PRIORITY_HOLD_SECONDS = 60        # keep the next limiter slot free for a priority poll due this soon
COVERAGE = ("Newest ~100 comments per poll of matched Reddit game threads, read from Reddit's public RSS "
            "feeds. A sample, not every comment. RSS carries no vote scores, so metrics are empty.")
SUBREDDIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_]{1,20}')
THREAD_PATH = re.compile(r'/r/([A-Za-z0-9_]{2,21})/comments/([a-z0-9]{2,12})(?:/([A-Za-z0-9_\-%]{0,120}))?/?')
SKIP_AUTHORS = re.compile(r'(?i)^(automoderator|\[deleted\])$|[-_]bot$')
EASTERN, PACIFIC = ZoneInfo('America/New_York'), ZoneInfo('America/Los_Angeles')


# --------------------------------------------------------------------------- rate limiter

def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def retry_after_seconds(value, now: float | None = None) -> float | None:
    """``Retry-After`` as seconds (numeric or HTTP-date form)."""
    if value in (None, ''):
        return None
    seconds = _float(value)
    if seconds is not None:
        return max(0.0, seconds)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now))
    except (TypeError, ValueError, OverflowError):
        return None


class RateLimiter:
    """One queue for every Reddit request in the process. It never bursts.

    After each response the next slot is pushed out to the largest of:
    ``min_interval``; ``reset + 1`` when ``x-ratelimit-remaining`` < 1 (else the
    window's even pace, ``reset / remaining``); ``Retry-After``; and, after a 429,
    403, 5xx or transport error, ``min_interval * 2**failures`` (capped at
    ``max_backoff``). A success resets the failure count.
    """
    MAX_DELAY = 6 * 3600.0

    def __init__(self, min_interval: float = 30.0, max_backoff: float = 900.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 state_path: Path | None = None, wall: Callable[[], float] = time.time):
        self.min_interval, self.max_backoff = float(min_interval), float(max_backoff)
        self.clock, self.sleep, self.wall = clock, sleep, wall
        self.next_at = 0.0
        self.failures = 0
        self.requests = 0
        self.last: dict = {'http_status': None, 'outcome': None, 'at': None, 'used': None, 'remaining': None,
                           'reset_seconds': None, 'retry_after_seconds': None, 'next_delay_seconds': None}
        self._lock_loop = None
        self._lock: asyncio.Lock | None = None
        # Reddit's window is per client, not per process: a restart or a manual run
        # must not reuse a slot the previous process already spent.
        self.state_path = state_path
        if state_path:
            try:
                saved = json.loads(Path(state_path).read_text())
                wait = min(float(saved['next_allowed_unix']) - self.wall(), self.MAX_DELAY)
                self.failures = int(saved.get('failures') or 0)
                if wait > 0:
                    self.next_at = self.clock() + wait
            except (OSError, ValueError, TypeError, KeyError):
                pass

    def _queue(self) -> asyncio.Lock:
        # asyncio.Lock waiters are served FIFO: this is the single request queue.
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    def wait_seconds(self) -> float:
        return max(0.0, self.next_at - self.clock())

    async def wait_ready(self):
        """Sleep until the next slot without taking it (lets callers pick fresh work)."""
        while (wait := self.wait_seconds()) > 0:
            await self.sleep(wait)

    async def request(self, send: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
        async with self._queue():
            await self.wait_ready()
            self.requests += 1
            try:
                response = await send()
            except Exception:
                self.record(None)
                raise
            self.record(response)
            return response

    def record(self, response: httpx.Response | None) -> float:
        """Update the schedule from one response (``None`` = transport error). Returns the delay."""
        headers = response.headers if response is not None else {}
        code = response.status_code if response is not None else None
        used, remaining = _float(headers.get('x-ratelimit-used')), _float(headers.get('x-ratelimit-remaining'))
        reset, retry = _float(headers.get('x-ratelimit-reset')), retry_after_seconds(headers.get('retry-after'))
        delay = self.min_interval
        if remaining is not None and reset is not None:
            delay = max(delay, reset + 1 if remaining < 1 else reset / remaining)
        failed = code is None or code in (403, 429) or code >= 500
        if failed:
            self.failures += 1
            delay = max(delay, min(self.max_backoff, self.min_interval * 2 ** self.failures))
            if code == 429 and reset is not None:
                delay = max(delay, reset + 1)
        else:
            self.failures = 0
        if retry is not None:
            delay = max(delay, retry)
        delay = min(delay, self.MAX_DELAY)
        now = self.clock()
        self.next_at = max(self.next_at, now + delay)
        outcome = ('ok' if code == 200 else 'rate_limited' if code == 429 else 'not_found' if code == 404
                   else 'error' if failed else 'unexpected')
        self.last = {'http_status': code, 'outcome': outcome, 'at': iso(), 'used': used, 'remaining': remaining,
                     'reset_seconds': reset, 'retry_after_seconds': retry, 'next_delay_seconds': round(delay, 1)}
        if self.state_path:
            try:
                path = Path(self.state_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'next_allowed_unix': self.wall() + (self.next_at - now),
                                            'failures': self.failures, 'last': self.last}))
            except OSError:
                pass
        return delay


# --------------------------------------------------------------------------- Atom parsing

def parse_atom(text: str) -> dict:
    """Reddit Atom -> {'title', 'entries': [{id, title, link, updated, published, author, content}]}."""
    if re.search(r'<!(DOCTYPE|ENTITY)', text[:4096], re.I):
        raise ValueError('Refusing an XML document with a DTD')
    root = ElementTree.fromstring(text.encode() if isinstance(text, str) else text)
    if root.tag != ATOM + 'feed':
        raise ValueError('Not an Atom feed')

    def value(node, name):
        child = node.find(ATOM + name)
        return (child.text or '').strip() if child is not None and child.text else ''

    entries = []
    for node in root.findall(ATOM + 'entry'):
        link = node.find(ATOM + 'link')
        author = node.find(f'{ATOM}author/{ATOM}name')
        name = (author.text or '').strip() if author is not None and author.text else ''
        entries.append({'id': value(node, 'id'), 'title': value(node, 'title'),
                        'link': link.get('href', '') if link is not None else '',
                        'updated': value(node, 'updated'), 'published': value(node, 'published'),
                        'author': re.sub(r'^/?u/', '', name), 'content': value(node, 'content')})
    return {'title': value(root, 'title'), 'entries': entries}


def thread_link(href: str) -> dict | None:
    """Validate a Reddit comments permalink -> {subreddit, id, slug}. Only reddit.com HTTPS links pass."""
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or parts.username or parts.password or not (host == 'reddit.com' or host.endswith('.reddit.com')):
        return None
    match = THREAD_PATH.match(parts.path)
    if not match:
        return None
    return {'subreddit': match.group(1), 'id': match.group(2), 'slug': match.group(3) or ''}


def comment_url(href: str) -> str | None:
    """Canonical https://www.reddit.com permalink for a comment entry link."""
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or parts.username or parts.password or not (host == 'reddit.com' or host.endswith('.reddit.com')):
        return None
    if not re.fullmatch(r'/r/[A-Za-z0-9_]{2,21}/comments/[a-z0-9]{2,12}/[A-Za-z0-9_\-%]{0,120}/[a-z0-9]{2,12}/?', parts.path):
        return None
    return BASE + parts.path


def entry_time(entry: dict) -> datetime | None:
    for key in ('published', 'updated'):
        try:
            return utc(entry[key]) if entry.get(key) else None
        except ValueError:
            continue
    return None


def normalize_comment(entry: dict, thread: dict, collected_at: str) -> dict | None:
    """One comment entry -> Post dict, or None for the thread post, bots, deletions and bad rows."""
    if not re.fullmatch(r't1_[a-z0-9]{2,12}', entry.get('id') or ''):
        return None  # t3_ is the thread post itself (usually a bot's box score)
    author = entry.get('author') or ''
    if not author or SKIP_AUTHORS.search(author):
        return None
    url = comment_url(entry.get('link') or '')
    try:
        created_at = iso(utc(entry.get('updated') or entry.get('published') or ''))
    except ValueError:
        return None
    text = plain_text(entry.get('content') or '')
    if not url or not text or text in ('[deleted]', '[removed]'):
        return None
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 1].rstrip() + '…'
    return {'id': 'reddit:' + entry['id'], 'provider': 'reddit', 'text': text, 'created_at': created_at,
            'collected_at': collected_at, 'url': url, 'author_display_name': 'u/' + author,
            'author_key': author_key('reddit', author.lower()), 'metrics': {},  # RSS has no scores
            'game_id': thread.get('game_id'), 'league': thread.get('league'),
            'subreddit': thread.get('subreddit'), 'thread_kind': thread.get('kind'),
            'thread_url': thread.get('url')}


# --------------------------------------------------------------------------- game-thread discovery

MONTHS = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun',
                                          'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}
_MONTH = r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?'


def thread_kind(title: str) -> str | None:
    text = ' '.join((title or '').split())
    if re.search(r'\bpre[\s-]?game\b', text, re.I):
        return None
    if re.search(r'\bpost[\s-]?game\s+(day\s+)?thread\b', text, re.I):
        return 'post_game_thread'
    if re.search(r'\bgame\s*(day\s+)?thread\b', text, re.I):
        return 'game_thread'
    return None


def title_dates(title: str, year_hint: int) -> set[date]:
    """Dates written in a thread title ("29 Sep 2026", "Sep 29", "9/29", "2026-09-29")."""
    found = set()
    text = (title or '').lower()

    def add(y, m, d):
        try:
            found.add(date(int(y) + (2000 if int(y) < 100 else 0), int(m), int(d)))
        except (TypeError, ValueError):
            pass
    for d, m, y in re.findall(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+' + _MONTH + r',?\s*(\d{4})?', text):
        add(y or year_hint, MONTHS[m], d)
    for m, d, y in re.findall(r'\b' + _MONTH + r'\s+(\d{1,2})(?:st|nd|rd|th)?\b,?\s*(\d{4})?', text):
        add(y or year_hint, MONTHS[m], d)
    for y, m, d in re.findall(r'\b(\d{4})-(\d{2})-(\d{2})\b', text):
        add(y, m, d)
    for m, d, y in re.findall(r'(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])', text):
        add(y or year_hint, m, d)
    return found


def team_aliases(league: str, team: dict) -> list[str]:
    """Names a thread title may use for ``team`` (ESPN names + the ranker's alias table)."""
    from bigplays.orchestrator.social_ranker import TEAM_ALIASES
    names = {team.get('short_name'), team.get('name')}
    names |= set(TEAM_ALIASES.get(league, {}).get(str(team.get('abbr') or '').upper(), []))
    return sorted({n for n in names if isinstance(n, str) and len(n) >= 3}, key=len, reverse=True)


def _mentions(title: str, aliases: list[str]) -> bool:
    return any(re.search(r'(?<![\w])' + re.escape(a) + r'(?![\w])', title, re.I) for a in aliases)


def team_subreddit(league: str, team: dict) -> str | None:
    return TEAM_SUBREDDITS.get(league, {}).get(str(team.get('abbr') or '').upper())


def match_thread(entry: dict, game: dict, subreddit: str) -> str | None:
    """The thread kind if ``entry`` is this game's (post) game thread in ``subreddit``, else None."""
    kind = thread_kind(entry.get('title', ''))
    link = thread_link(entry.get('link', ''))
    if not kind or not link or not re.fullmatch(r't3_[a-z0-9]{2,12}', entry.get('id') or ''):
        return None
    try:
        start = utc(game['start_utc'])
    except (KeyError, TypeError, ValueError):
        return None
    posted = entry_time(entry)
    if posted is None or not start - timedelta(hours=18) <= posted <= start + timedelta(hours=10):
        return None
    # Game threads go up before/around first pitch; post-game threads after it.
    if kind == 'game_thread' and posted > start + timedelta(hours=2):
        return None
    if kind == 'post_game_thread' and posted < start + timedelta(hours=1):
        return None
    dates = title_dates(entry['title'], start.year)
    if dates and not dates & {start.astimezone(EASTERN).date(), start.astimezone(PACIFIC).date(), start.date()}:
        return None  # yesterday's thread in the same series
    league = game.get('league')
    home, away = game.get('home') or {}, game.get('away') or {}
    title = entry['title']
    home_hit, away_hit = _mentions(title, team_aliases(league, home)), _mentions(title, team_aliases(league, away))
    sub = subreddit.lower()
    own = {(team_subreddit(league, home) or '').lower(): away_hit, (team_subreddit(league, away) or '').lower(): home_hit}
    if sub in own:
        # A team's own subreddit may omit its own name; it must name the opponent.
        return kind if own[sub] else None
    return kind if home_hit and away_hit else None


def find_threads(entries: list[dict], game: dict, subreddit: str) -> dict[str, dict]:
    """Best matching thread per kind: {'game_thread': thread, 'post_game_thread': thread}."""
    start = utc(game['start_utc'])
    best: dict[str, tuple[float, dict]] = {}
    for entry in entries:
        kind = match_thread(entry, game, subreddit)
        if not kind:
            continue
        link = thread_link(entry['link'])
        posted = entry_time(entry)
        # Doubleheaders: prefer the game thread posted nearest first pitch, the earliest post-game thread.
        rank = abs((posted - start).total_seconds() + 3600) if kind == 'game_thread' else (posted - start).total_seconds()
        path = f"/r/{link['subreddit']}/comments/{link['id']}/" + (f"{link['slug']}/" if link['slug'] else '')
        thread = {'id': link['id'], 'kind': kind, 'subreddit': link['subreddit'], 'title': entry['title'][:300],
                  'url': BASE + path, 'posted_at': iso(posted), 'game_id': str(game.get('game_id')),
                  'league': game.get('league')}
        if kind not in best or rank < best[kind][0]:
            best[kind] = (rank, thread)
    return {kind: thread for kind, (_, thread) in best.items()}


# --------------------------------------------------------------------------- HTTP client

class RedditRSS:
    """Fetches Reddit Atom feeds through the shared limiter. Returns (outcome, parsed feed | None)."""

    def __init__(self, limiter: RateLimiter | None = None, client: httpx.AsyncClient | None = None,
                 user_agent: str | None = None):
        self.limiter = limiter or shared_limiter()
        self.user_agent = user_agent or settings.reddit_rss_user_agent or DEFAULT_USER_AGENT
        self._client = client

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()

    async def feed(self, path: str, params: dict) -> tuple[str, dict | None]:
        if not (path.startswith('/r/') and path.endswith('/.rss')):
            raise ValueError('Only Reddit RSS feeds are read')
        headers = {'User-Agent': self.user_agent, 'Accept': 'application/atom+xml, application/xml;q=0.9'}
        try:
            response = await self.limiter.request(lambda: self.client().get(BASE + path, params=params, headers=headers))
        except httpx.HTTPError:
            return 'error', None
        outcome = self.limiter.last['outcome']
        if response.status_code != 200:
            return outcome, None
        try:
            return 'ok', parse_atom(response.text)
        except (ValueError, ElementTree.ParseError):
            return 'error', None

    async def listing(self, subreddit: str, limit: int = 100):
        if not SUBREDDIT.fullmatch(subreddit):
            raise ValueError('Invalid subreddit name')
        return await self.feed(f'/r/{subreddit}/new/.rss', {'limit': limit})

    async def comments(self, thread: dict, limit: int = 100):
        link = thread_link(thread.get('url', ''))
        if not link:
            raise ValueError('Invalid thread URL')
        path = f"/r/{link['subreddit']}/comments/{link['id']}/" + (f"{link['slug']}/" if link['slug'] else '') + '.rss'
        return await self.feed(path, {'limit': limit, 'sort': 'new'})


_LIMITER: RateLimiter | None = None


def shared_limiter() -> RateLimiter:
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = RateLimiter(min_interval=settings.reddit_rss_min_interval_seconds,
                               state_path=Path(settings.reddit_rss_state_path).with_name('reddit_rss_limiter.json'))
    return _LIMITER


# --------------------------------------------------------------------------- poller

async def live_games() -> list[dict]:
    """Today's NFL/MLB scoreboard games (all states); the poller filters by status."""
    from bigplays.ingest import gamecast
    local = datetime.now(PACIFIC)
    dates = [local.strftime('%Y%m%d')] + ([(local - timedelta(days=1)).strftime('%Y%m%d')] if local.hour < 4 else [])
    games = []
    for league in LEAGUES:
        if league not in [str(name).lower() for name in settings.leagues]:
            continue
        for day in dates:
            try:
                games.extend(await gamecast.fetch_scoreboard(league, day))
            except Exception as error:  # noqa: BLE001 - one league failing must not stop the other
                log.warning('reddit rss: %s scoreboard unavailable: %s', league, error)
    return games


def captured_game_ids() -> set[str]:
    """Games the live capture agent is recording (they get clips, so they go first)."""
    try:
        status = json.loads((settings.agent_dir / 'status.json').read_text())
        return {str(g['game']['game_id']) for g in status.get('games', []) if g.get('game', {}).get('game_id')}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return set()


def _slim_game(game: dict) -> dict:
    team = lambda t: {k: (t or {}).get(k) for k in ('abbr', 'name', 'short_name')}
    return {'game_id': str(game.get('game_id')), 'league': game.get('league'), 'start_utc': game.get('start_utc'),
            'status': game.get('status'), 'home': team(game.get('home')), 'away': team(game.get('away'))}


class RedditRSSPoller:
    """Discovers live games' Reddit threads and polls their comments, one request at a time."""

    def __init__(self, reader: RedditRSS | None = None, *, scoreboard=None, state_path: Path | None = None,
                 clock: Callable[[], float] = time.time, captured=captured_game_ids,
                 max_games: int | None = None, max_threads: int | None = None,
                 sleep: Callable[[float], Awaitable[None]] | None = None):
        self._reader = reader
        self.scoreboard = scoreboard or live_games
        self.state_path = Path(state_path or settings.reddit_rss_state_path)
        # The capture agent asks for a prompt poll of a game thread here (hype gate).
        self.priority_path = priority_path(self.state_path)
        self._sleep = sleep
        self.clock, self.captured = clock, captured
        self.max_games = max_games or settings.reddit_rss_max_games
        self.max_threads = max_threads or settings.reddit_rss_max_threads
        self.games: dict[str, dict] = {}
        self.listings: dict[str, dict] = {}   # lower(subreddit) -> {'at', 'outcome'}
        self.skip_subs: dict[str, float] = {}  # lower(subreddit) -> retry at (404s)
        self.listeners: list[Callable[[str, str, list[dict]], None]] = []
        self.last_scoreboard = 0.0
        self.checked_at: str | None = None
        self.last_ok_at: str | None = None
        self.error: str | None = None
        self.loaded = False

    @property
    def reader(self) -> RedditRSS:
        if self._reader is None:
            self._reader = RedditRSS()
        return self._reader

    # ------------------------------------------------------------------ persistence
    def load(self):
        self.loaded = True
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return
        for game_id, record in (data.get('games') or {}).items():
            if isinstance(record, dict) and isinstance(record.get('game'), dict):
                record['posts'] = {p['id']: p for p in record.get('posts', []) if isinstance(p, dict) and p.get('id')}
                record.setdefault('threads', {})
                self.games[str(game_id)] = record
        self.prune()

    def save(self):
        data = {'version': 1, 'saved_at': iso(), 'note': COVERAGE, 'games': {
            game_id: {**{k: v for k, v in record.items() if k != 'posts'},
                      'posts': sorted(record['posts'].values(), key=lambda p: p['created_at'])}
            for game_id, record in self.games.items()}}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.state_path)

    async def persist(self):
        # Saved after every poll (not only when comments are new) so the capture agent,
        # a separate process, can see each poll's time, outcome and coverage.
        try:
            await asyncio.to_thread(self.save)
        except OSError as error:
            log.warning('reddit rss: could not persist posts: %s', error)

    def prune(self):
        cutoff = self.clock() - POST_RETENTION_SECONDS
        for game_id, record in list(self.games.items()):
            posts = {k: p for k, p in record['posts'].items() if (_ts(p.get('created_at')) or 0) >= cutoff}
            record['posts'] = dict(sorted(posts.items(), key=lambda kv: kv[1]['created_at'])[-MAX_POSTS_PER_GAME:])
            if not record['posts'] and (record.get('last_live') or 0) < cutoff:
                del self.games[game_id]

    # ------------------------------------------------------------------ games
    def update_games(self, games: list[dict]):
        now = self.clock()
        for game in games:
            if game.get('league') not in LEAGUES or not game.get('game_id') or not game.get('start_utc'):
                continue
            game_id = str(game['game_id'])
            record = self.games.get(game_id)
            if game.get('status') == 'in':
                record = record or self.games.setdefault(game_id, {'threads': {}, 'posts': {}})
                record.update(game=_slim_game(game), status='in', last_live=now, ended_at=None)
            elif game.get('status') == 'post' and record and record.get('status') == 'in':
                record.update(game=_slim_game(game), status='post', ended_at=now)

    def active_games(self) -> list[dict]:
        now, captured = self.clock(), self.captured()
        active = [r for r in self.games.values()
                  if (r.get('status') == 'in' and now - (r.get('last_live') or 0) < STALE_LIVE_SECONDS)
                  or (r.get('status') == 'post' and now - (r.get('ended_at') or 0) < POST_GAME_GRACE_SECONDS)]
        active.sort(key=lambda r: (r['game']['game_id'] not in captured, r.get('status') != 'in',
                                   r['game'].get('start_utc') or ''))
        return active[:self.max_games]

    def subreddits(self, record: dict) -> list[str]:
        game, league = record['game'], record['game']['league']
        subs = [team_subreddit(league, game.get('home') or {}), team_subreddit(league, game.get('away') or {}),
                LEAGUE_SUBREDDITS.get(league)]
        return list(dict.fromkeys(s for s in subs if s))

    def wanted_kind(self, record: dict) -> str:
        return 'game_thread' if record.get('status') == 'in' else 'post_game_thread'

    def threads_to_poll(self, record: dict, per_game: int) -> list[dict]:
        threads = list(record['threads'].values())
        wanted = self.wanted_kind(record)
        chosen = [t for t in threads if t['kind'] == wanted]
        if wanted == 'post_game_thread' and not chosen:
            chosen = [t for t in threads if t['kind'] == 'game_thread']  # final reactions until a PGT exists
        order = {s.lower(): i for i, s in enumerate(self.subreddits(record))}
        chosen.sort(key=lambda t: order.get(t['subreddit'].lower(), 99))
        return chosen[:per_game]

    def poll_plan(self) -> list[tuple[dict, dict]]:
        games = self.active_games()
        if not games:
            return []
        per_game = max(1, min(THREADS_PER_GAME, self.max_threads // len(games)))
        return [(record, thread) for record in games for thread in self.threads_to_poll(record, per_game)]

    def priority_thread(self, record: dict) -> dict | None:
        """The game's busiest pollable thread over the last 10 min (home team's on a tie)."""
        threads = self.threads_to_poll(record, THREADS_PER_GAME)
        if not threads:
            return None
        cutoff = self.clock() - 600
        recent = {t['url']: 0 for t in threads}
        for post in record['posts'].values():
            if post.get('thread_url') in recent and (_ts(post.get('created_at')) or 0) >= cutoff:
                recent[post['thread_url']] += 1
        return max(threads, key=lambda t: (recent[t['url']], -threads.index(t)))

    def due_priority(self) -> tuple[float, dict, dict] | None:
        """(not_before, record, thread) of the most urgent unmet priority request, if any.

        A request is met once its thread has been polled at or after ``not_before``.
        """
        best = None
        requests = read_priority(self.priority_path, self.clock())
        active = {id(r) for r in self.active_games()} if requests else set()
        for request in requests.values():
            record = self.games.get(str(request.get('game_id')))
            if not record or id(record) not in active:
                continue
            thread = self.priority_thread(record)
            not_before = _float(request.get('not_before')) or 0.0
            if not thread or (thread.get('polled_at') or 0) >= not_before:
                continue
            if best is None or not_before < best[0]:
                best = (not_before, record, thread)
        return best

    def next_task(self) -> tuple | None:
        now = self.clock()
        due = self.due_priority()
        if due and due[0] <= now:
            return ('comments', due[1], due[2])  # priority lane: still waits for the shared limiter
        for record in self.active_games():
            kind = self.wanted_kind(record)
            for sub in self.subreddits(record):
                key = sub.lower()
                if self.skip_subs.get(key, 0) > now:
                    continue
                have = any(t['subreddit'].lower() == key and t['kind'] == kind for t in record['threads'].values())
                listing = self.listings.get(key)
                if not have and (not listing or now - listing['at'] >= REDISCOVER_SECONDS):
                    return ('listing', sub)
        plan = self.poll_plan()
        if not plan:
            return None
        record, thread = min(plan, key=lambda pair: pair[1].get('polled_at') or 0)
        return ('comments', record, thread)

    # ------------------------------------------------------------------ work
    async def discover(self, subreddit: str) -> str:
        outcome, feed = await self.reader.listing(subreddit)
        now = self.clock()
        self.listings[subreddit.lower()] = {'at': now, 'outcome': outcome}
        self.checked_at = iso()
        if outcome in ('not_found', 'unexpected'):  # misspelt/private/banned sub (404, redirect): skip it
            self.skip_subs[subreddit.lower()] = now + 6 * 3600
            return outcome
        if outcome != 'ok':
            self.error = f'r/{subreddit} listing: {outcome}'
            return outcome
        self.error, self.last_ok_at = None, iso()
        changed = False
        for record in self.games.values():
            if subreddit.lower() not in [s.lower() for s in self.subreddits(record)]:
                continue
            for kind, thread in find_threads(feed['entries'], record['game'], subreddit).items():
                key = f"{thread['subreddit'].lower()}:{kind}"
                existing = record['threads'].get(key)
                if not existing or existing['id'] != thread['id']:
                    record['threads'][key] = thread
                    changed = True
        if changed:
            await self.persist()
        return outcome

    async def poll(self, record: dict, thread: dict) -> list[dict]:
        outcome, feed = await self.reader.comments(thread)
        polled = self.clock()
        thread['polled_at'] = polled
        thread['last_outcome'] = outcome
        self.checked_at = iso()
        # Poll log: an ok poll saw every comment from its oldest entry up to the poll time,
        # which tells readers which minutes of the thread are covered (RSS = newest ~100).
        entry = {'at': round(polled, 3), 'outcome': outcome}
        if outcome == 'ok':
            times = [t for e in feed['entries'] if re.fullmatch(r't1_[a-z0-9]{2,12}', e.get('id') or '')
                     and (t := _ts(e.get('updated') or e.get('published'))) is not None]
            entry.update(entries=len(times), oldest=min(times) if times else None,
                         newest=max(times) if times else None)
        thread['polls'] = (thread.get('polls') or [])[-(POLL_LOG - 1):] + [entry]
        if outcome in ('not_found', 'unexpected'):  # removed thread: forget it so discovery can replace it
            record['threads'] = {k: t for k, t in record['threads'].items() if t['id'] != thread['id']}
            await self.persist()
            return []
        if outcome != 'ok':
            self.error = f"r/{thread['subreddit']} comments: {outcome}"
            await self.persist()
            return []
        self.error, self.last_ok_at = None, iso()
        collected = iso()
        new = []
        for entry in feed['entries']:
            post = normalize_comment(entry, thread, collected)
            if post and post['id'] not in record['posts']:  # dedupe by Atom entry id
                record['posts'][post['id']] = post
                new.append(post)
        thread['comments_seen'] = thread.get('comments_seen', 0) + len(new)
        thread['last_batch'] = sum(1 for e in feed['entries'] if e.get('id', '').startswith('t1_'))
        if new:
            self.prune()
        await self.persist()
        if new:
            for listener in list(self.listeners):
                try:
                    listener(record['game']['league'], record['game']['game_id'], new)
                except Exception:  # noqa: BLE001 - listeners are optional enrichment
                    log.exception('reddit rss listener failed')
        return new

    async def step(self) -> bool:
        """Do at most one Reddit request. Returns False when there is nothing to fetch."""
        if not self.loaded:
            await asyncio.to_thread(self.load)
        await self.reader.limiter.wait_ready()
        if self.clock() - self.last_scoreboard >= SCOREBOARD_SECONDS:
            self.last_scoreboard = self.clock()
            self.update_games(await self.scoreboard())
        # A priority poll is due shortly: hold this slot for it rather than spend it now
        # and make the priority poll wait a whole rate-limit interval.
        while (due := self.due_priority()) and 0 < (wait := due[0] - self.clock()) <= PRIORITY_HOLD_SECONDS:
            await (self._sleep or asyncio.sleep)(min(wait, 5.0))
        task = self.next_task()
        if task is None:
            return False
        if task[0] == 'listing':
            await self.discover(task[1])
        else:
            await self.poll(task[1], task[2])
        return True

    async def run(self):
        while True:
            try:
                busy = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - keep polling; report the failure
                self.error = f'poller: {type(error).__name__}'
                log.exception('reddit rss poller step failed')
                busy = False
            if not busy:
                await asyncio.sleep(30)

    # ------------------------------------------------------------------ read side
    def posts(self, league: str | None = None, game_id: str | None = None) -> list[dict]:
        rows = [p for gid, record in self.games.items()
                if (league is None or record['game'].get('league') == league) and (game_id is None or gid == str(game_id))
                for p in record['posts'].values()]
        return sorted(rows, key=lambda p: p['created_at'], reverse=True)

    def status(self, league: str | None = None) -> dict:
        base = {'used_in_feed': True, 'source': 'rss', 'coverage': COVERAGE,
                'user_agent': self.reader.user_agent if self._reader else (settings.reddit_rss_user_agent or DEFAULT_USER_AGENT),
                'min_interval_seconds': settings.reddit_rss_min_interval_seconds}
        if not settings.reddit_rss_enabled:
            return {**base, 'status': 'disabled', 'used_in_feed': False, 'fetched_at': None, 'error': None,
                    'retry_after_seconds': None, 'threads': []}
        plan = [(r, t) for r, t in self.poll_plan() if league is None or r['game']['league'] == league]
        limiter = self.reader.limiter if self._reader else _LIMITER
        last = limiter.last if limiter else {}
        wait = limiter.wait_seconds() if limiter else 0
        if self.checked_at is None:
            state = 'not_checked'
        elif last.get('outcome') == 'rate_limited':
            state = 'rate_limited'
        elif self.error:
            state = 'error'
        elif not plan:
            state = 'no_live_threads'
        else:
            state = 'ok'
        threads = [{'game_id': r['game']['game_id'], 'subreddit': t['subreddit'], 'kind': t['kind'],
                    'title': t['title'], 'url': t['url'], 'comments_seen': t.get('comments_seen', 0),
                    'last_outcome': t.get('last_outcome'),
                    'last_polled': iso(datetime.fromtimestamp(t['polled_at'], timezone.utc)) if t.get('polled_at') else None}
                   for r, t in plan]
        return {**base, 'status': state, 'fetched_at': self.last_ok_at, 'checked_at': self.checked_at,
                'error': self.error, 'retry_after_seconds': round(wait) if state == 'rate_limited' else None,
                'threads': threads, 'live_games': len({t['game_id'] for t in threads}),
                'posts_in_memory': len(self.posts(league)),
                'rate_limit': {k: last.get(k) for k in ('http_status', 'used', 'remaining', 'reset_seconds',
                                                         'retry_after_seconds', 'next_delay_seconds')}}


def priority_path(state_path: Path | None = None) -> Path:
    return Path(state_path or settings.reddit_rss_state_path).with_name(PRIORITY_FILE)


def read_priority(path: Path, now: float | None = None) -> dict[str, dict]:
    """Unexpired priority requests: {key: {game_id, league, not_before, until, requested_at}}."""
    now = time.time() if now is None else now
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    requests = data.get('requests') if isinstance(data, dict) else None
    return {str(k): r for k, r in (requests or {}).items()
            if isinstance(r, dict) and (_float(r.get('until')) or 0) > now and r.get('game_id')}


def _write_priority(path: Path, requests: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'version': 1, 'requests': requests}), encoding='utf-8')
    temp.replace(path)


def request_priority(key: str, game_id: str, league: str, not_before: float, until: float,
                     path: Path | None = None, now: float | None = None) -> None:
    """Ask the server's RSS poller to poll ``game_id``'s thread at/after ``not_before``.

    Written by the capture agent (the only writer); read by the poller, which serves it
    through the same shared rate limiter as every other Reddit request.
    """
    path = priority_path() if path is None else path
    now = time.time() if now is None else now
    requests = read_priority(path, now)
    requests[str(key)] = {'game_id': str(game_id), 'league': league, 'not_before': float(not_before),
                          'until': float(until), 'requested_at': now}
    _write_priority(path, requests)


def clear_priority(key: str, path: Path | None = None, now: float | None = None) -> None:
    path = priority_path() if path is None else path
    requests = read_priority(path, now)
    if requests.pop(str(key), None) is not None or Path(path).exists():
        _write_priority(path, requests)


def _ts(value) -> float | None:
    try:
        return utc(value).timestamp()
    except (TypeError, ValueError, AttributeError):
        return None


poller = RedditRSSPoller()


def main():
    """Manual check: discover one game's threads and fetch comments once (a few spaced requests)."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--league', choices=LEAGUES, default='mlb')
    parser.add_argument('--game-id', required=True)
    parser.add_argument('--subreddit', action='append', help='Subreddit(s) to search (default: home team sub)')
    parser.add_argument('--polls', type=int, default=1)
    args = parser.parse_args()

    async def run():
        from bigplays.ingest import gamecast
        games = [g for g in await gamecast.fetch_scoreboard(args.league) if g['game_id'] == args.game_id]
        if not games:
            raise SystemExit('Game not on today\'s scoreboard')
        runner = RedditRSSPoller(scoreboard=lambda: asyncio.sleep(0, games), state_path=Path(settings.reddit_rss_state_path))
        runner.load()
        runner.update_games(games)
        record = runner.games[args.game_id]
        for sub in args.subreddit or runner.subreddits(record)[:1]:
            print('listing', sub, await runner.discover(sub))
        print(json.dumps(list(record['threads'].values()), indent=2))
        for thread in list(record['threads'].values())[:args.polls]:
            new = await runner.poll(record, thread)
            print('comments', thread['url'], thread.get('last_outcome'), 'new:', len(new))
        await runner.reader.close()
    asyncio.run(run())


if __name__ == '__main__':
    main()
