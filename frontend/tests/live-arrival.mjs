// Live-arrival browser test. Self-contained: serves the built dashboard (frontend/dist) with
// `vite preview` and mocks every /api call, so it runs whether or not the backend is up and never
// writes into the real highlight catalog.
//
// Covers:
//  - a genuinely new live clip arriving over SSE shows at the top without a page reload;
//  - EventSource reconnects (each resends `hello` + a replayed `highlight`) never duplicate it;
//  - after a browser reload the clip is still present exactly once and still on top;
//  - provenance badges/filter (LIVE CAPTURE / OFFICIAL UPLOAD, including an old NFL.com import);
//  - MLB inning/count/outs + distinct event / publication / import times, and
//    "Event time unavailable" when the event time is missing;
//  - the social panel with a working endpoint and with a 404.
//
// Usage: npm run build && node tests/live-arrival.mjs   (BIGPLAYS_TEST_BASE=<url> to use a running server)
import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
let preview
let base = process.env.BIGPLAYS_TEST_BASE
if (!base) {
  const port = 4300 + Math.floor(Math.random() * 500)
  preview = spawn('npx', ['vite', 'preview', '--port', String(port), '--strictPort', '--host', '127.0.0.1'], { cwd: root, stdio: 'pipe' })
  base = `http://127.0.0.1:${port}`
  for (let i = 0; ; i++) {
    try { if ((await fetch(base)).ok) break } catch { /* not up yet */ }
    if (i > 100) throw new Error('vite preview did not start')
    await new Promise(r => setTimeout(r, 150))
  }
}

const iso = seconds => new Date(Date.now() - seconds * 1000).toISOString()
const clip = (over) => ({
  game_id: 'g1', league: 'nfl', title: 'Clip', description: '', reasons: [], base_score: .6, combined_score: .6, tags: [],
  file: null, poster: null, away: 'KC', home: 'BUF', away_score: 7, home_score: 3, period: 'Q2', clock: '4:12', ...over,
})
// An old NFL.com import: it still carries demo: true from the retired replay mode and must show as an official upload.
const replay = clip({ event_id: 'nfl-replay-1', title: 'Replay touchdown', demo: true, imported: true, replay_dataset: 'nfl-2026-week3',
  occurred_utc: '2026-09-21T20:14:03Z', published_utc: '2026-09-21T20:40:00Z', received_utc: iso(7200) })
const mlbUpload = clip({ event_id: 'mlb-upload-1', game_id: 'm1', league: 'mlb', title: 'Murakami single', imported: true,
  replay_dataset: 'mlb-2026-09-29', away: 'CWS', home: 'HOU', away_score: 5, home_score: 2, period: 'Top 7', clock: '',
  inning: 7, inning_half: 'top', balls: 1, strikes: 0, outs: 0, count_context: 'before pitch',
  occurred_utc: '2026-09-29T23:17:23.104Z', published_utc: '2026-09-30T00:26:14.345Z', received_utc: '2026-09-30T00:27:58.476Z',
  timestamp_source: 'MLB play event startTime', source: { channel: 'MLB', url: 'https://www.mlb.com/video/x' } })
const mlbNoTime = clip({ ...mlbUpload, event_id: 'mlb-upload-2', title: 'Unmatched homer', occurred_utc: null, timestamp_source: null,
  inning: 3, inning_half: 'bottom', balls: 3, strikes: 2, outs: 2, count_context: undefined })
const live = clip({ event_id: 'live-capture-1', title: 'LIVE: 62-yard strike', source_kind: 'live_capture',
  occurred_utc: iso(40), received_utc: iso(5), ts: Date.now() / 1000 - 5, llm: { verdict: true, hype_score: .93, tags: [], title: '', rationale: 'x' } })

// Mock backend state. `recent` is what a fresh hello returns (newest first, like the catalog).
const server = { recent: [replay, mlbUpload, mlbNoTime], replayOnConnect: [], connections: 0 }
const sse = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const errors = []
async function newPage({ social = 'ok' } = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, timezoneId: 'America/Los_Angeles' })
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/stream') {
      server.connections++
      // Each response ends immediately, so EventSource reconnects after `retry` ms: every
      // connection is a reconnect that re-sends the full hello plus any replayed events.
      const body = 'retry: 250\n\n' + sse('hello', { mode: 'live', games: [], recent: server.recent })
        + server.replayOnConnect.map(({ __event, ...h }) => sse(__event ?? 'highlight', h)).join('')
      return route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' }, body })
    }
    if (url.pathname.startsWith('/api/social/')) {
      if (social === '404') return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Not Found"}' })
      const providers = {
        mastodon: { status: 'ok', note: 'Public timeline search', used_in_feed: true, serving_stale_posts: true },
        x: { status: 'disabled_paid', note: 'X API requires a paid tier', used_in_feed: false },
        reddit: { status: 'missing_credentials', note: 'Set REDDIT_CLIENT_ID', used_in_feed: false },
      }
      if (url.pathname === '/api/social/status') return route.fulfill({ json: { ok: true, cache_ttl_seconds: 60, clip_gate: {}, providers } })
      const posts = [
        { id: 'p1', provider: 'mastodon', author_display_name: 'Chiefs Fan', author_key: 'mastodon:@fan', text: 'That throw was unreal',
          url: 'https://mastodon.social/@fan/1', created_at: iso(60), collected_at: iso(30), metrics: { likes: 1200, reposts: 40, replies: 88 },
          relevance: 'play_evidence', play_matches: [{ clip_id: 'nfl-replay-1', players: ['Mahomes'], teams: ['KC'], actions: ['touchdown'],
            seconds_after_play: 42, confidence: 'high', method: 'player+action' }] },
        { id: 'p2', provider: 'mastodon', author_display_name: 'someone', author_key: 'mastodon:@s', text: 'Great day for football',
          url: null, created_at: iso(600), collected_at: iso(30), metrics: { likes: 3 }, relevance: 'general_chatter', play_matches: [] },
        { id: 'p2', provider: 'mastodon', author_display_name: 'someone', text: 'dup', relevance: 'general_chatter' },
      ]
      return route.fulfill({ json: { ok: true, league: url.searchParams.get('league'), fetched_at: iso(90), cached: true, stale: true,
        cache_age_seconds: 90, cache_ttl_seconds: 60, count: 2, play_evidence_count: 1, coverage: {}, posts, providers } })
    }
    if (url.pathname === '/api/mlb/games') return route.fulfill({ json: { enabled: false, games: [], date: '2026-09-29' } })
    if (url.pathname === '/api/agent') return route.fulfill({ json: {} })
    if (url.pathname === '/api/live-streams') return route.fulfill({ json: { streams: [] } })
    if (url.pathname === '/api/recording') return route.fulfill({ json: {} })
    return route.fulfill({ json: {} })
  })
  return page
}

const ids = page => page.locator('.feed-item:not(.pending)').evaluateAll(items => items.map(i => i.dataset.eventId))
async function waitConnections(n) {
  const target = server.connections + n
  for (let i = 0; server.connections < target; i++) {
    if (i > 200) throw new Error('EventSource did not reconnect')
    await new Promise(r => setTimeout(r, 50))
  }
}

try {
  const page = await newPage()
  await page.goto(base + '/?view=clips&league=all')
  await page.getByLabel('auto-play new').uncheck()
  await page.waitForFunction(() => document.querySelectorAll('.feed-item:not(.pending)').length === 3)
  await waitConnections(2)
  assert.deepEqual(await ids(page), ['nfl-replay-1', 'mlb-upload-1', 'mlb-upload-2'], 'reconnects must not duplicate history')

  // provenance badges and filter
  assert.equal(await page.locator('[data-event-id="nfl-replay-1"] .source-pill').textContent(), 'OFFICIAL UPLOAD')
  assert.equal(await page.locator('[data-event-id="mlb-upload-1"] .source-pill').textContent(), 'OFFICIAL UPLOAD')
  await page.getByLabel('Filter by source').selectOption('official_upload')
  assert.deepEqual(await ids(page), ['nfl-replay-1', 'mlb-upload-1', 'mlb-upload-2'])
  await page.getByLabel('Filter by source').selectOption('live_capture')
  assert.deepEqual(await ids(page), [])
  await page.getByLabel('Filter by source').selectOption('all')

  // MLB state + distinct timestamps
  await page.locator('[data-event-id="mlb-upload-1"]').click()
  assert.equal(await page.locator('.player .clock').textContent(), 'Top 7 · 1–0 count (before pitch) · 0 outs')
  assert.equal(await page.locator('.player .source-pill').textContent(), 'OFFICIAL UPLOAD')
  assert.equal(await page.locator('.original-play-time').getAttribute('datetime'), mlbUpload.occurred_utc)
  assert.match(await page.locator('.original-play-time').textContent(), /Sep 29, 2026.*4:17:23 PM PDT/)
  assert.equal(await page.locator('.published-time').getAttribute('datetime'), mlbUpload.published_utc)
  assert.match(await page.locator('.published-time').textContent(), /5:26:14 PM PDT/)
  assert.equal(await page.locator('.import-time').getAttribute('datetime'), mlbUpload.received_utc)
  await page.getByText(/Imported by BigPlays:/).waitFor()
  await page.locator('[data-event-id="mlb-upload-2"]').click()
  assert.equal(await page.locator('.player .clock').textContent(), 'Bottom 3 · 3–2 count · 2 outs')
  assert.equal(await page.locator('.original-play-time').textContent(), 'Event time unavailable')
  assert.equal(await page.locator('.original-play-time').getAttribute('datetime'), null)
  assert.equal(await page.locator('[data-event-id="mlb-upload-2"] .feed-event-time time').textContent(), 'Event time unavailable')

  // social panel
  await page.locator('.provider-status[data-provider="mastodon"]').filter({ hasText: 'Connected · stale posts' }).waitFor()
  await page.locator('.provider-status[data-provider="x"]').filter({ hasText: 'Paid API · disabled' }).waitFor()
  await page.locator('.provider-status[data-provider="reddit"]').filter({ hasText: 'Missing credentials' }).waitFor()
  await page.locator('.social-cache.stale').filter({ hasText: 'Showing stale cached posts · 90s old' }).waitFor()
  const evidence = page.getByRole('group', { name: 'Play evidence' })
  assert.equal(await evidence.locator('.social-post').count(), 1)
  assert.equal(await page.getByRole('group', { name: 'General chatter' }).locator('.social-post').count(), 1)
  assert.ok((await evidence.locator('.social-post').textContent()).includes('Chiefs Fan'))
  assert.match(await evidence.locator('.play-match').textContent(), /Replay touchdown.*high confidence · \+42s after play/)
  await evidence.locator('.play-match button').click()                 // jumps to the matched clip
  assert.equal(await page.locator('.feed-item.active').getAttribute('data-event-id'), 'nfl-replay-1')
  await evidence.locator('.social-post.current').waitFor()

  // late social enrichment via highlight_update updates the open clip in place
  server.replayOnConnect = [{ __event: 'highlight_update', ...replay, social_score: .71,
    social_enrichment: { status: 'matched', post_count: 1, sources: [] } }]
  await page.locator('.social-enrichment').filter({ hasText: 'Social evidence: 1 matched posts · social score 0.71' }).waitFor()
  server.replayOnConnect = []
  server.recent = server.recent.map(h => h.event_id === replay.event_id ? { ...h, social_score: .71,
    social_enrichment: { status: 'matched', post_count: 1, sources: [] } } : h)
  await waitConnections(2)
  assert.equal((await ids(page)).length, 3)

  // live arrival without reload
  await page.evaluate(() => { window.__noReload = true })
  server.replayOnConnect = [live]               // backend broadcasts the new clip (before any hello includes it)
  await page.locator('[data-event-id="live-capture-1"]').waitFor({ timeout: 5000 })
  server.recent = [live, ...server.recent]      // ...and it is now persisted, so later hellos include it too
  assert.equal(await page.evaluate(() => window.__noReload), true, 'clip must appear without a page reload')
  assert.equal((await ids(page))[0], 'live-capture-1', 'new live clip must be at the top')
  assert.equal(await page.locator('[data-event-id="live-capture-1"] .source-pill').textContent(), 'LIVE CAPTURE')
  await page.locator('.toast').filter({ hasText: 'NEW LIVE CAPTURE' }).waitFor()
  await waitConnections(4)
  assert.deepEqual(await ids(page), ['live-capture-1', 'nfl-replay-1', 'mlb-upload-1', 'mlb-upload-2'], 'reconnects must not duplicate or drop clips')

  // a session-only clip (not yet in the server snapshot) must survive a reconnect
  const sessionOnly = { ...live, event_id: 'live-capture-2', title: 'LIVE: pick six', received_utc: iso(1) }
  server.replayOnConnect = [sessionOnly]
  await page.locator('[data-event-id="live-capture-2"]').waitFor({ timeout: 5000 })
  server.replayOnConnect = []
  await waitConnections(3)
  assert.deepEqual((await ids(page)).slice(0, 2), ['live-capture-2', 'live-capture-1'])
  assert.equal((await ids(page)).length, 5)

  // reload: the persisted clip is still there once and still on top
  server.replayOnConnect = [live]
  await page.reload()
  await page.waitForFunction(() => document.querySelectorAll('.feed-item:not(.pending)').length >= 4)
  assert.equal(await page.evaluate(() => window.__noReload), undefined, 'page actually reloaded')
  await waitConnections(3)
  assert.deepEqual(await ids(page), ['live-capture-1', 'nfl-replay-1', 'mlb-upload-1', 'mlb-upload-2'])
  await page.close()

  // social endpoint missing -> panel degrades, rest of the page still works
  const degraded = await newPage({ social: '404' })
  await degraded.goto(base + '/?view=clips&league=nfl')
  await degraded.getByText(/Social feed unavailable/).waitFor()
  await degraded.locator('.provider-status.unavailable').waitFor()
  await degraded.locator('[data-event-id="live-capture-1"]').waitFor()
  await degraded.close()

  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ liveArrival: 'passed', reconnectDedupe: 'passed', sessionOnlySurvivesReconnect: 'passed',
    reloadPersistence: 'passed', provenance: 'passed', mlbTimes: 'passed', socialPanel: 'passed', social404: 'passed',
    sseConnections: server.connections, errors }, null, 2))
} finally {
  await browser.close()
  preview?.kill()
}
