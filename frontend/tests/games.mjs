// Scores + play-by-play browser test. Self-contained: serves the built dashboard (frontend/dist)
// with `vite preview` and mocks every /api call (docs/games-api-contract.md), so it runs whether
// or not the backend is up.
//
// Covers:
//  - Scores is the default view; scoreboard renders MLB (diamond, B-S-O, clip badge) and NFL
//    (down & distance, possession); live games sort first, then upcoming, then final;
//  - league tabs + date picker drive the URL and the request;
//  - opening a game: header, linescore, play-by-play grouped by period (latest first + toggle),
//    scoring highlights, an attached clip rendering and actually playing, alternate clips,
//    "Viral plays only", clips_unmatched ("More clips from this game");
//  - polling picks up a new play (highlighted, on top) and a newly attached clip ("NEW CLIP"),
//    while keeping the reader's scroll position;
//  - deep-link reload (?game=...), league fallback when the link has no league, Clips tab;
//  - "Scores unavailable" / "Game unavailable" when the endpoints 404 / 500;
//  - a 390px iPhone viewport with no horizontal overflow.
//
// Usage: npm run build && node tests/games.mjs   (BIGPLAYS_TEST_BASE=<url> to use a running server)
import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const shots = process.env.BIGPLAYS_SHOTS_DIR
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

// ------------------------------------------------------------------ fixtures (ESPN-shaped)
const logo = (league, abbr) => `https://a.espncdn.com/i/teamlogos/${league}/500/${abbr.toLowerCase()}.png`
const team = (league, id, abbr, name, short_name, color, alt_color, score, record, winner = null) =>
  ({ id, abbr, name, short_name, logo: logo(league, abbr), color, alt_color, score, record, winner })

const mlbGame = (over) => ({ league: 'mlb', venue: null, broadcast: null, period: null, period_label: null, clock: null,
  situation: null, last_play_text: null, clip_count: 0, viral_count: 0, ...over })
const bosNyy = mlbGame({
  game_id: '401907924', status: 'in', status_detail: 'Top 7th', start_utc: '2026-09-29T23:05:00Z',
  venue: 'Yankee Stadium', broadcast: 'NBC', period: 7, period_label: 'Top 7', clock: null,
  away: team('mlb', '2', 'BOS', 'Boston Red Sox', 'Red Sox', 'bd3039', '0d2b56', 3, '87-72'),
  home: team('mlb', '10', 'NYY', 'New York Yankees', 'Yankees', '003087', 'e4002c', 5, '93-66'),
  situation: { balls: 1, strikes: 2, outs: 1, on_first: true, on_second: false, on_third: true, batter: 'Trevor Story', pitcher: 'Luke Weaver' },
  last_play_text: 'Trevor Story struck out swinging.', clip_count: 3, viral_count: 1,
})
const houSea = mlbGame({
  game_id: '401907927', status: 'in', status_detail: 'Bot 2nd', start_utc: '2026-09-30T01:40:00Z',
  venue: 'T-Mobile Park', period: 2, period_label: 'Bottom 2',
  away: team('mlb', '18', 'HOU', 'Houston Astros', 'Astros', '002d62', 'eb6e1f', 0, '84-75'),
  home: team('mlb', '12', 'SEA', 'Seattle Mariners', 'Mariners', '005c5c', '0c2c56', 0, '88-71'),
  situation: { balls: 0, strikes: 0, outs: 0, on_first: false, on_second: false, on_third: false, batter: 'Julio Rodríguez', pitcher: 'Framber Valdez' },
})
const ladSf = mlbGame({
  game_id: '401907926', status: 'pre', status_detail: '9/29 - 7:15 PM PDT', start_utc: '2026-09-30T02:15:00Z',
  venue: 'Oracle Park', broadcast: 'MLB.TV',
  away: team('mlb', '19', 'LAD', 'Los Angeles Dodgers', 'Dodgers', '005a9c', 'ef3e42', null, '95-64'),
  home: team('mlb', '26', 'SF', 'San Francisco Giants', 'Giants', 'fd5a1e', '27251f', null, '79-80'),
})
const tbTor = mlbGame({
  game_id: '401907925', status: 'post', status_detail: 'Final', start_utc: '2026-09-29T23:07:00Z',
  away: team('mlb', '30', 'TB', 'Tampa Bay Rays', 'Rays', '092c5c', '8fbce6', 2, '76-83', false),
  home: team('mlb', '14', 'TOR', 'Toronto Blue Jays', 'Blue Jays', '134a8e', '1d2d5c', 4, '90-69', true), clip_count: 1,
})

const atlGb = {
  game_id: '401872948', league: 'nfl', status: 'in', status_detail: '5:13 - 3rd Quarter', start_utc: '2026-09-29T00:15:00Z',
  venue: 'Lambeau Field', broadcast: 'ESPN', period: 3, period_label: 'Q3', clock: '5:13',
  away: team('nfl', '1', 'ATL', 'Atlanta Falcons', 'Falcons', 'a71930', '000000', 17, '1-2'),
  home: team('nfl', '9', 'GB', 'Green Bay Packers', 'Packers', '204e32', 'ffb612', 21, '2-1'),
  situation: { down: 3, distance: 4, yard_line_text: 'GB 35', possession: 'GB', is_red_zone: false, down_distance_text: '3rd & 4 at GB 35' },
  last_play_text: 'Josh Jacobs rush up the middle for 3 yards to the GB 35.', clip_count: 2, viral_count: 1,
}
const nyjMia = {
  game_id: '401872941', league: 'nfl', status: 'post', status_detail: 'Final/OT', start_utc: '2026-09-28T17:00:00Z',
  venue: 'Hard Rock Stadium', broadcast: 'CBS', period: 5, period_label: 'OT', clock: '0:00',
  away: team('nfl', '20', 'NYJ', 'New York Jets', 'Jets', '115740', 'ffffff', 23, '1-3', false),
  home: team('nfl', '15', 'MIA', 'Miami Dolphins', 'Dolphins', '008e97', 'fc4c02', 26, '2-2', true),
  situation: null, last_play_text: null, clip_count: 0, viral_count: 0,
}

const clipRef = (over) => ({ title: 'Clip', video_url: '/clips/test.webm', poster_url: null, duration_seconds: 14,
  source_kind: 'live_capture', social_score: null, occurred_utc: '2026-09-30T01:12:40Z', published_utc: null, ...over })
const judgeClip = clipRef({ event_id: 'clip-judge-hr', title: 'Aaron Judge 3-run HR (412 ft)', social_score: 0.82,
  poster_url: '/clips/judge-poster.svg', occurred_utc: '2026-09-30T01:12:40Z' })
const judgeOfficial = clipRef({ event_id: 'clip-judge-hr-mlb', title: 'Judge crushes a 3-run homer', source_kind: 'official_upload',
  duration_seconds: 38, published_utc: '2026-09-30T01:30:00Z' })
const casasClip = clipRef({ event_id: 'clip-casas-hr', title: 'Triston Casas 2-run HR', source_kind: 'replay', duration_seconds: 21 })

const mlbPlay = (seq, period_label, text, away_score, home_score, over = {}) => ({
  play_id: `401907924${String(seq).padStart(3, '0')}`, sequence: seq, period: Number(period_label.split(' ')[1]), period_label,
  clock: null, text, type: over.type ?? 'Play Result', scoring: false, team_abbr: null, away_score, home_score,
  wallclock_utc: null, is_key_play: false, mlb: { batter: null, pitcher: null, balls: 1, strikes: 1, outs: 0, pitch_count: 3 },
  clip: null, viral: false, viral_reason: null, ...over,
})
const mlbPlays = () => [
  mlbPlay(1, 'Top 1', 'Jarren Duran grounded out to second.', 0, 0, { type: 'Groundout', mlb: { balls: 0, strikes: 1, outs: 1 } }),
  mlbPlay(2, 'Top 1', 'Rafael Devers homered to right (398 ft).', 1, 0, { type: 'Home Run', scoring: true, team_abbr: 'BOS' }),
  mlbPlay(3, 'Bottom 1', 'Aaron Judge walked.', 1, 0, { type: 'Walk', mlb: { balls: 4, strikes: 1, outs: 1 } }),
  mlbPlay(4, 'Bottom 1', 'Cody Bellinger doubled to right, Judge scored.', 1, 1, { type: 'Double', scoring: true, team_abbr: 'NYY' }),
  mlbPlay(5, 'Top 5', 'Triston Casas homered to right center (421 ft), Wilyer Abreu scored.', 3, 1, { type: 'Home Run', scoring: true, team_abbr: 'BOS' }),
  mlbPlay(6, 'Bottom 5', 'Anthony Volpe homered to left (371 ft).', 3, 2, { type: 'Home Run', scoring: true, team_abbr: 'NYY' }),
  mlbPlay(7, 'Bottom 6', 'Aaron Judge homered to left (412 ft), Bellinger and Rice scored.', 3, 5, {
    type: 'Home Run', scoring: true, team_abbr: 'NYY', is_key_play: true, clip: judgeClip, alternate_clips: [judgeOfficial],
    viral: true, viral_reason: 'clip + social score 0.82', mlb: { batter: 'Aaron Judge', pitcher: 'Garrett Crochet', balls: 2, strikes: 1, outs: 1, pitch_count: 4 } }),
  mlbPlay(8, 'Top 7', 'Trevor Story struck out swinging.', 3, 5, { type: 'Strikeout', mlb: { balls: 1, strikes: 3, outs: 1 } }),
]
const mlbLinescore = {
  periods: [[1, 1], [0, 0], [0, 0], [0, 0], [2, 1], [0, 3], [0, null]].map(([away, home], i) => ({ label: String(i + 1), away, home })),
  totals: { away: { R: 3, H: 6, E: 0 }, home: { R: 5, H: 8, E: 1 } },
}
const unmatched = [clipRef({ event_id: 'clip-bat-flip', title: 'Judge admires it, Yankee Stadium erupts', source_kind: 'official_upload', duration_seconds: 29 }),
  clipRef({ event_id: 'clip-missing-file', title: 'Weaver escapes the jam', source_kind: 'official_upload', video_url: null,
    poster_url: 'https://i.ytimg.com/vi/abc123xyz00/mqdefault.jpg' })]

const nflPlay = (seq, period_label, clock, text, away_score, home_score, over = {}) => ({
  play_id: `401872948${String(seq).padStart(3, '0')}`, sequence: seq, period: Number(period_label.slice(1)), period_label, clock, text,
  type: 'Rush', scoring: false, team_abbr: null, away_score, home_score, wallclock_utc: null, is_key_play: false, mlb: null,
  clip: null, viral: false, viral_reason: null, ...over,
})
const nflPlays = [
  nflPlay(1, 'Q1', '15:00', 'Brandon McManus kicks 65 yards from GB 35 to end zone, Touchback.', 0, 0, { type: 'Kickoff' }),
  nflPlay(2, 'Q1', '9:41', 'Michael Penix Jr. pass short right to Drake London for 12 yards, TOUCHDOWN.', 7, 0, { type: 'Passing Touchdown', scoring: true, team_abbr: 'ATL' }),
  nflPlay(3, 'Q2', '2:04', 'Jordan Love pass deep left to Jayden Reed for 45 yards, TOUCHDOWN.', 14, 21, {
    type: 'Passing Touchdown', scoring: true, team_abbr: 'GB', clip: clipRef({ event_id: 'clip-reed-td', title: 'Love to Reed, 45-yard TD', social_score: 0.91 }),
    alternate_clips: [clipRef({ event_id: 'clip-reed-td-fv', title: 'Field View' })],
    viral: true, viral_reason: 'clip + social score 0.91' }),
  nflPlay(4, 'Q3', '5:13', 'Josh Jacobs rush up the middle for 3 yards to the GB 35.', 17, 21),
]
const nflLinescore = { periods: [{ label: '1', away: 7, home: 7 }, { label: '2', away: 7, home: 14 }, { label: '3', away: 3, home: 0 }],
  totals: { away: { score: 17 }, home: { score: 21 } } }

// ------------------------------------------------------------------ mock backend
const server = { plays: mlbPlays(), requests: [], gamesStatus: 200, detailStatus: 200, gameUpdate: null }
const todayLA = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit' })
  .format(new Date()).replaceAll('-', '')
const shift = (key, days) => { const d = new Date(Date.UTC(+key.slice(0, 4), +key.slice(4, 6) - 1, +key.slice(6, 8) + days)); return d.toISOString().slice(0, 10).replaceAll('-', '') }

let testVideo = null // webm bytes recorded in-browser (no binary fixture needed)
const posterSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#003087"/><text x="20" y="100" fill="#fff" font-size="28">JUDGE HR</text></svg>'
const logoSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><circle cx="32" cy="32" r="30" fill="#888"/></svg>'

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const errors = []

async function recordWebm() {
  const page = await browser.newPage()
  await page.goto(base + '/?view=clips-none')   // any same-origin page works; we only need a canvas
  const b64 = await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = 160; canvas.height = 90
    const ctx = canvas.getContext('2d')
    const stream = canvas.captureStream(25)
    const rec = new MediaRecorder(stream, { mimeType: 'video/webm' })
    const chunks = []
    rec.ondataavailable = e => chunks.push(e.data)
    let f = 0
    const draw = setInterval(() => { ctx.fillStyle = `hsl(${(f++ * 12) % 360} 70% 50%)`; ctx.fillRect(0, 0, 160, 90) }, 40)
    rec.start(100)
    await new Promise(r => setTimeout(r, 2500))
    rec.stop(); clearInterval(draw)
    await new Promise(r => { rec.onstop = r })
    const buf = new Uint8Array(await new Blob(chunks, { type: 'video/webm' }).arrayBuffer())
    let s = ''; for (const b of buf) s += String.fromCharCode(b)
    return btoa(s)
  })
  await page.close()
  return Buffer.from(b64, 'base64')
}

async function newPage({ width = 1280, height = 900, mobile = false, poll = { scores: 500, detail: 500 } } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, timezoneId: 'America/Los_Angeles',
    isMobile: mobile, hasTouch: mobile, deviceScaleFactor: mobile ? 3 : 1 })
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(p => { window.__BIGPLAYS_POLL_MS__ = p }, poll)
  await page.route('https://i.ytimg.com/**', route => route.fulfill({ contentType: 'image/svg+xml', body: posterSvg }))
  await page.route('https://a.espncdn.com/**', route => route.fulfill({ contentType: 'image/svg+xml', body: logoSvg }))
  await page.route('**/clips/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('.svg')) return route.fulfill({ contentType: 'image/svg+xml', body: posterSvg })
    return route.fulfill({ status: 200, contentType: 'video/webm', body: testVideo })
  })
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url())
    server.requests.push(url.pathname + url.search)
    if (url.pathname === '/api/games') {
      if (server.gamesStatus !== 200) return route.fulfill({ status: server.gamesStatus, json: { ok: false, error: 'scoreboard unavailable' } })
      const league = url.searchParams.get('league')
      const date = url.searchParams.get('date')
      const games = league === 'mlb' ? (date === todayLA ? [tbTor, ladSf, bosNyy, houSea] : []) : [nyjMia, atlGb]
      return route.fulfill({ json: { ok: true, league, date, updated_utc: new Date().toISOString(), games } })
    }
    const m = /^\/api\/games\/(nfl|mlb)\/(\w+)$/.exec(url.pathname)
    if (m) {
      if (server.detailStatus !== 200) return route.fulfill({ status: server.detailStatus, json: { ok: false, error: 'ESPN upstream error' } })
      const [, league, id] = m
      if (league === 'mlb' && id === bosNyy.game_id) {
        const last = server.plays.at(-1)
        return route.fulfill({ json: { ok: true, game: { ...bosNyy, last_play_text: last.text }, linescore: mlbLinescore,
          updated_utc: new Date().toISOString(), plays: server.plays, clips_unmatched: unmatched } })
      }
      if (league === 'nfl' && id === atlGb.game_id) {
        return route.fulfill({ json: { ok: true, game: atlGb, linescore: nflLinescore, updated_utc: new Date().toISOString(),
          plays: nflPlays, clips_unmatched: [] } })
      }
      return route.fulfill({ status: 404, json: { ok: false, error: 'game not found' } })
    }
    if (url.pathname === '/api/stream') {
      const update = server.gameUpdate; server.gameUpdate = null
      return route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' },
        body: 'retry: 300\n\nevent: hello\ndata: {"mode":"live","games":[],"recent":[]}\n\n'
          + (update ? `event: game_update\ndata: ${JSON.stringify(update)}\n\n` : '') })
    }
    if (url.pathname === '/api/live-streams') return route.fulfill({ json: { streams: [] } })
    if (url.pathname === '/api/mlb/games') return route.fulfill({ json: { enabled: false, games: [] } })
    return route.fulfill({ json: {} })
  })
  return page
}

const cardIds = page => page.locator('.game-card').evaluateAll(els => els.map(e => e.dataset.gameId))
const groupLabels = page => page.locator('.period-group').evaluateAll(els => els.map(e => e.dataset.period))
const rowIds = page => page.locator('.play-row').evaluateAll(els => els.map(e => e.dataset.playId))
const pid = seq => `401907924${String(seq).padStart(3, '0')}`
const noHorizontalOverflow = page => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
const results = {}

try {
  testVideo = await recordWebm()
  assert.ok(testVideo.length > 1000, 'recorded a test video')

  // ---------------------------------------------------------------- scoreboard (MLB default)
  const page = await newPage()
  await page.goto(base + '/')
  await page.locator('.game-card').first().waitFor()
  assert.equal(await page.getByRole('tab', { name: 'MLB' }).getAttribute('aria-selected'), 'true')
  assert.equal(await page.locator('.view-tabs a[aria-current="page"]').textContent(), 'Scores', 'Scores is the default view')
  assert.ok(server.requests.includes(`/api/games?league=mlb&date=${todayLA}`), 'requests today (LA) scoreboard')
  assert.deepEqual(await cardIds(page), ['401907924', '401907927', '401907926', '401907925'], 'live first, then upcoming, then final')
  const nyy = page.locator('[data-game-id="401907924"]')
  assert.equal((await nyy.locator('.status').textContent()).trim(), 'Top 7')
  assert.equal(await nyy.locator('[data-base="first"]').getAttribute('data-on'), 'true')
  assert.equal(await nyy.locator('[data-base="second"]').getAttribute('data-on'), 'false')
  assert.equal(await nyy.locator('[data-base="third"]').getAttribute('data-on'), 'true')
  assert.equal(await nyy.locator('.bso .count').textContent(), '1-2')
  assert.equal(await nyy.locator('.outs i.on').count(), 1)
  assert.equal(await nyy.locator('.clip-badge').textContent(), '🎬 3 clips')
  assert.match(await nyy.textContent(), /BOS.*87-72.*3.*NYY.*93-66.*5/)
  assert.equal((await page.locator('[data-game-id="401907927"] .status').textContent()).trim(), 'Bot 2', '"Bottom 2" normalized')
  assert.equal(await page.locator('[data-game-id="401907926"] .status').textContent(), '7:15 PM', 'start time in local time')
  assert.equal(await page.locator('[data-game-id="401907926"] .clip-badge').count(), 0)
  assert.equal(await page.locator('[data-game-id="401907925"] .status').textContent(), 'Final')
  assert.equal(await page.locator('[data-game-id="401907925"] .gc-team.loser .gc-abbr').textContent(), 'TB')
  results.mlbScoreboard = results.liveOrdering = 'passed'

  // ---------------------------------------------------------------- NFL tab
  await page.getByRole('tab', { name: 'NFL' }).click()
  await page.locator('[data-game-id="401872948"]').waitFor()
  assert.match(page.url(), /league=nfl/)
  assert.deepEqual(await cardIds(page), ['401872948', '401872941'])
  const gb = page.locator('[data-game-id="401872948"]')
  assert.equal((await gb.locator('.status').textContent()).trim(), 'Q3 · 5:13')
  assert.equal(await gb.locator('.down-distance').textContent(), '3rd & 4 at GB 35')
  assert.equal(await gb.locator('.possession').getAttribute('aria-label'), 'GB ball')
  assert.equal(await gb.locator('.clip-badge').textContent(), '🎬 2 clips')
  assert.equal(await page.locator('[data-game-id="401872941"] .status').textContent(), 'Final/OT')
  results.nflScoreboard = 'passed'

  // ---------------------------------------------------------------- date picker + empty state
  await page.getByRole('tab', { name: 'MLB' }).click()
  await page.getByLabel('Previous day').click()
  await page.getByText('No MLB games').waitFor()
  assert.match(page.url(), new RegExp(`date=${shift(todayLA, -1)}`))
  assert.ok(server.requests.includes(`/api/games?league=mlb&date=${shift(todayLA, -1)}`))
  assert.match(await page.locator('.date-label span').textContent(), /^Yesterday/)
  await page.getByLabel('Next day').click()
  await page.locator('[data-game-id="401907924"]').waitFor()
  assert.doesNotMatch(page.url(), /date=/)
  results.datePicker = 'passed'

  // ---------------------------------------------------------------- open a game
  await page.locator('[data-game-id="401907924"]').click()
  await page.locator('.gd-header[data-status]').waitFor()
  assert.match(page.url(), /view=scores&league=mlb&game=401907924/)
  assert.match(await page.locator('.gd-header').textContent(), /BOS.*3.*Top 7.*NYY.*5/)
  assert.equal(await page.locator('.gd-header .bso .count').textContent(), '1-2')
  assert.match(await page.locator('.gd-header .matchup').textContent(), /Trevor Story.*Luke Weaver/)
  assert.deepEqual(await page.locator('.linescore thead th').allTextContents(), ['', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'R', 'H', 'E'])
  assert.deepEqual(await page.locator('.linescore tbody tr').nth(1).locator('td').allTextContents(), ['1', '0', '0', '0', '1', '3', '–', '–', '–', '5', '8', '1'])
  results.openGame = 'passed'

  // ---------------------------------------------------------------- grouping + order
  assert.deepEqual(await groupLabels(page), ['Top 7', 'Bot 6', 'Bot 5', 'Top 5', 'Bot 1', 'Top 1'])
  assert.equal((await rowIds(page))[0], pid(8), 'latest play first')
  assert.equal(await page.locator('.play-row[data-scoring="true"]').count(), 5)
  assert.equal(await page.locator(`[data-play-id="${pid(8)}"] .play-when`).textContent(), '1-3, 1 out')
  assert.match(await page.locator(`[data-play-id="${pid(7)}"] .play-score`).textContent(), /BOS 3NYY 5/)
  assert.equal(await page.locator(`[data-play-id="${pid(7)}"] .play-score .scored`).textContent(), 'NYY 5')
  await page.getByLabel('Toggle play order').click()
  assert.deepEqual(await groupLabels(page), ['Top 1', 'Bot 1', 'Top 5', 'Bot 5', 'Bot 6', 'Top 7'])
  assert.equal((await rowIds(page))[0], pid(1))
  await page.getByLabel('Toggle play order').click()
  results.playGrouping = 'passed'

  // ---------------------------------------------------------------- attached clip renders + plays
  const judge = page.locator(`[data-play-id="${pid(7)}"]`)
  assert.ok(await judge.evaluate(el => el.classList.contains('viral') && el.classList.contains('scoring')))
  assert.equal(await judge.locator('.source-pill').textContent(), 'LIVE CAPTURE')
  assert.equal(await judge.locator('.social-score').textContent(), '🔥 0.82')
  assert.equal(await judge.locator('.clip-duration').textContent(), '0:14')
  assert.equal(await judge.locator('.viral-reason').textContent(), 'clip + social score 0.82')
  assert.deepEqual(await judge.locator('.alt-clips button').allTextContents(), ['LIVE CAPTURE', 'OFFICIAL UPLOAD'])
  await judge.getByRole('button', { name: /Play clip: Aaron Judge/ }).click()
  await judge.locator('video').waitFor()
  assert.equal(await judge.locator('video').getAttribute('src'), '/clips/test.webm')
  await page.waitForFunction(pid7 => { const v = document.querySelector(`[data-play-id="${pid7}"] video`); return v && !v.paused && v.currentTime > 0 }, pid(7), { timeout: 8000 })
  await judge.locator('.alt-clips button', { hasText: 'OFFICIAL UPLOAD' }).click()
  assert.equal(await judge.locator('.clip-card').getAttribute('data-clip-id'), 'clip-judge-hr-mlb')
  assert.equal(await judge.locator('.clip-meta .source-pill').textContent(), 'OFFICIAL UPLOAD')
  results.clipPlays = results.alternateClips = 'passed'

  // ---------------------------------------------------------------- viral-only + unmatched
  await page.getByRole('button', { name: /Viral plays only/ }).click()
  assert.deepEqual(await rowIds(page), [pid(7)])
  assert.deepEqual(await groupLabels(page), ['Bot 6'])
  await page.getByRole('button', { name: /Viral plays only/ }).click()
  assert.equal((await rowIds(page)).length, 8)
  results.viralFilter = 'passed'
  const more = page.getByRole('region', { name: 'More clips from this game' })
  assert.equal(await more.locator('h2').textContent(), 'More clips from this game')
  assert.equal(await more.locator('.clip-card').count(), 2)
  assert.match(await more.locator('[data-clip-id="clip-bat-flip"]').textContent(), /OFFICIAL UPLOAD.*Judge admires it/)
  const missing = more.locator('[data-clip-id="clip-missing-file"]')
  assert.equal(await missing.locator('.clip-failed').textContent(), 'Video unavailable', 'null video_url shows the poster + notice')
  assert.ok(await missing.locator('.clip-poster').isDisabled())
  assert.match(await missing.locator('.clip-poster').getAttribute('style'), /i\.ytimg\.com/)
  results.clipsUnmatched = 'passed'

  // ---------------------------------------------------------------- polling: new play + newly attached clip, scroll kept
  await page.setViewportSize({ width: 1280, height: 420 })   // short viewport so the row can sit at the top
  await page.locator(`[data-play-id="${pid(4)}"]`).evaluate(el => el.scrollIntoView({ block: 'start' }))
  await page.waitForTimeout(150)
  const before = await page.locator(`[data-play-id="${pid(4)}"]`).evaluate(el => el.getBoundingClientRect().top)
  const scrollBefore = await page.evaluate(() => window.scrollY)
  await page.evaluate(() => { window.__noReload = true })
  server.plays = [...server.plays.map(p => p.sequence === 5 ? { ...p, clip: casasClip, viral: true, viral_reason: 'clip attached' } : p),
    mlbPlay(9, 'Top 7', 'Wilyer Abreu doubled (38) on a line drive to deep right.', 3, 5, { type: 'Double', mlb: { balls: 0, strikes: 0, outs: 1 } })]
  await page.locator(`.play-row.is-new[data-play-id="${pid(9)}"]`).waitFor({ timeout: 5000 })
  await page.locator(`[data-play-id="${pid(5)}"] .new-clip-flash`).filter({ hasText: 'NEW CLIP' }).waitFor({ timeout: 5000 })
  assert.equal((await rowIds(page))[0], pid(9), 'new play appears at the top')
  assert.equal(await page.locator(`[data-play-id="${pid(5)}"] .source-pill`).textContent(), 'MLB REPLAY')
  await page.waitForTimeout(200)
  const after = await page.locator(`[data-play-id="${pid(4)}"]`).evaluate(el => el.getBoundingClientRect().top)
  assert.ok(Math.abs(after - before) < 3, `scroll position kept (row moved ${before} -> ${after})`)
  assert.ok(await page.evaluate(() => window.scrollY) > scrollBefore + 150, 'page scrolled to compensate for the new row + attached clip above')
  assert.equal(await page.evaluate(() => window.__noReload), true)
  await page.setViewportSize({ width: 1280, height: 900 })
  results.pollingNewPlay = results.pollingNewClip = results.scrollKept = 'passed'

  // ---------------------------------------------------------------- deep-link reload + back + clips tab
  await page.reload()
  await page.locator('.gd-header[data-status]').waitFor()
  assert.match(page.url(), /game=401907924/)
  assert.equal((await rowIds(page)).length, 9)
  await page.getByRole('button', { name: '‹ Scores' }).click()
  await page.locator('[data-game-id="401907924"]').waitFor()
  assert.doesNotMatch(page.url(), /game=/)
  await page.goBack()
  await page.locator('.gd-header[data-status]').waitFor()
  await page.getByRole('link', { name: 'Clips' }).click()
  await page.locator('.feed').waitFor()
  assert.match(page.url(), /view=clips/)
  await page.getByRole('link', { name: 'Scores' }).click()
  await page.locator('.game-card').first().waitFor()
  await page.close()

  const deep = await newPage()
  await deep.goto(base + '/?game=401872948')     // no league: falls back from mlb (404) to nfl
  await deep.locator('.gd-header[data-status]').waitFor()
  assert.match(deep.url(), /league=nfl/)
  assert.deepEqual(await groupLabels(deep), ['Q3', 'Q2', 'Q1'])
  assert.equal(await deep.locator('[data-play-id="401872948004"] .play-when').textContent(), '5:13')
  assert.equal(await deep.locator('.gd-header .down-distance').textContent(), '3rd & 4 at GB 35')
  assert.deepEqual(await deep.locator('.linescore thead th').allTextContents(), ['', '1', '2', '3', '4', 'T'])
  assert.deepEqual(await deep.locator('.linescore tbody tr').nth(1).locator('td').allTextContents(), ['7', '14', '0', '–', '21'])
  assert.deepEqual(await deep.locator('[data-play-id="401872948003"] .alt-clips button').allTextContents(),
    ['Love to Reed, 45-yard TD', 'Field View'], 'same-source alternates are labelled by title')
  await deep.close()
  results.deepLinkLeagueFallback = 'passed'

  // ---------------------------------------------------------------- SSE game_update refetches immediately
  const sse = await newPage({ poll: { scores: 600000, detail: 600000 } })   // polling effectively off
  await sse.goto(base + '/?view=scores&league=mlb&game=401907924')
  await sse.locator('.gd-header[data-status]').waitFor()
  const rowsBefore = (await rowIds(sse)).length
  server.plays = [...server.plays, mlbPlay(10, 'Top 7', 'Ceddanne Rafaela homered to left (402 ft), Abreu scored.', 5, 5,
    { type: 'Home Run', scoring: true, team_abbr: 'BOS', clip: clipRef({ event_id: 'clip-rafaela-hr', title: 'Rafaela ties it' }), viral: true })]
  server.gameUpdate = { league: 'mlb', game_id: '401907924' }
  await sse.locator(`.play-row.is-new[data-play-id="${pid(10)}"] .new-clip-flash`).waitFor({ timeout: 5000 })
  assert.equal((await rowIds(sse)).length, rowsBefore + 1)
  await sse.close()
  server.plays = server.plays.filter(p => p.sequence !== 10)
  results.sseGameUpdate = 'passed'
  results.deepLinkReload = 'passed'

  // ---------------------------------------------------------------- degraded endpoints
  server.gamesStatus = 404
  const down = await newPage()
  await down.goto(base + '/?view=scores&league=nfl')
  await down.getByRole('alert').filter({ hasText: 'Scores unavailable' }).waitFor()
  server.gamesStatus = 200
  server.detailStatus = 500
  await down.goto(base + '/?view=scores&league=mlb&game=401907924')
  await down.getByRole('alert').filter({ hasText: 'Game unavailable' }).waitFor()
  server.detailStatus = 200
  await down.getByRole('button', { name: 'Try again' }).click()
  await down.locator('.gd-header[data-status]').waitFor()
  await down.close()
  results.degraded = 'passed'

  // ---------------------------------------------------------------- 390px iPhone
  const phone = await newPage({ width: 390, height: 844, mobile: true })
  await phone.goto(base + '/?view=scores&league=mlb')
  await phone.locator('.game-card').first().waitFor()
  assert.ok(await noHorizontalOverflow(phone), 'scoreboard fits 390px')
  const cardBox = await phone.locator('.game-card').first().boundingBox()
  assert.ok(cardBox.x >= 15 && cardBox.x + cardBox.width <= 375, 'cards single column with 16px gutters')
  if (shots) { mkdirSync(shots, { recursive: true }); await phone.screenshot({ path: `${shots}/scores-390.png`, fullPage: true }) }
  await phone.locator('[data-game-id="401907924"]').click()
  await phone.locator('.gd-header[data-status]').waitFor()
  assert.ok(await noHorizontalOverflow(phone), 'game detail fits 390px')
  const poster = await phone.locator(`[data-play-id="${pid(7)}"] .clip-poster`).boundingBox()
  assert.ok(poster.width > 200 && poster.x + poster.width <= 390, 'inline clip poster is large and on-screen')
  if (shots) await phone.screenshot({ path: `${shots}/game-390.png`, fullPage: true })
  await phone.getByRole('tab', { name: 'NFL' }).count().then(n => assert.equal(n, 0, 'league tabs hidden in detail'))
  await phone.close()
  results.mobile390 = 'passed'

  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ ...results, apiRequests: server.requests.length, errors }, null, 2))
} finally {
  await browser.close()
  preview?.kill()
}
