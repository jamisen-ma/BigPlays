// Video frame test for the Expo web app (react-native-web, expo-video, expo-image).
//
// 1. A web Metro must be running (see tests/web-screens.mjs), e.g.
//      cd mobile && EXPO_PUBLIC_API_URL=http://127.0.0.1:8000 npx expo start --web --port 8082
// 2. node tests/video-frame.mjs
//      BASE=http://localhost:8082  API=http://127.0.0.1:8000  GAME=mlb/401907896  PLAY_MATCH='Velázquez homered'
//
// At 390 / 768 / 1280 / 1600px: opens the game, records the clip poster's box, taps play, waits ~1s,
// records the <video> box and asserts poster == video (±1px), 16:9 (±1%), object-fit: contain,
// the "source · reason" row below the frame, frame <= 720px, native controls, no fullscreen, and that
// the one tap started playback. Then resizes 320 -> 2000px while playing, checks Instagram-style
// autoplay (plays muted when scrolled into view, one at a time, pauses off screen, resumes), and the Clips tab
// cards and the full-screen player the same way. Shared checks: ../../frontend/tests/video-frame-helpers.mjs.
// Screenshots: screenshots/video-fix-expo-*.png. Exits 1 on any failure.
import { mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { MAX_FRAME, WIDTHS, autoplayOnScroll, fmt, is169, makeChecker, measure, posterToVideo, resizeSweep, round, sameBox } from '../../frontend/tests/video-frame-helpers.mjs'

const require = createRequire(new URL('../../frontend/package.json', import.meta.url))
const { chromium } = require('@playwright/test')

const BASE = (process.env.BASE ?? 'http://localhost:8082').replace(/\/+$/, '')
const API = (process.env.API ?? 'http://127.0.0.1:8000').replace(/\/+$/, '')
const [league, gameId] = (process.env.GAME ?? 'mlb/401907896').split('/')
const PLAY_MATCH = new RegExp(process.env.PLAY_MATCH ?? 'Velázquez homered', 'i')
const shots = fileURLToPath(new URL('../screenshots/', import.meta.url))
mkdirSync(shots, { recursive: true })

const detail = await (await fetch(`${API}/api/games/${league}/${gameId}`)).json()
const withVideo = (detail.plays ?? []).filter(p => p.clip?.video_url)
const play = withVideo.find(p => PLAY_MATCH.test(p.text)) ?? withVideo[0]
if (!play) { console.error(`no play with a playable clip in ${league}/${gameId}`); process.exit(1) }
console.log(`${detail.game.away.abbr}@${detail.game.home.abbr}: "${play.text}" (${play.play_id})`)

const sel = { frame: '[data-testid="clip-media"]', poster: '[aria-label^="Play clip"]', posterImg: '[aria-label^="Play clip"] img',
  video: '[data-testid="clip-media"] video', meta: '[data-testid="clip-meta"]', overlays: '[aria-label^="Play clip"]' }
const { failures, check } = makeChecker('expo ')
const table = []
const autoplayRows = []
const pageErrors = []
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const open = async (width, height = 900, { autoplay = false } = {}) => {
  const page = await browser.newPage({ viewport: { width, height } })
  page.on('pageerror', e => pageErrors.push(e.message))
  // Clips autoplay on scroll; the poster -> click checks need the poster, so opt out except in the autoplay section.
  if (!autoplay) await page.addInitScript(() => { window.__BIGPLAYS_AUTOPLAY__ = false })
  return page
}

/** The play-by-play is a virtualized SectionList: scroll it until the row is rendered. */
async function findRow(page, width, { poster = true } = {}) {
  const row = page.locator(`[data-testid="play-${play.play_id}"]`)
  await page.getByText('Play-by-play').first().waitFor({ timeout: 240_000 })
  for (let i = 0; i < 80 && !(await row.count()); i++) {
    await page.mouse.move(width / 2, 500)
    await page.mouse.wheel(0, 700)
    await page.waitForTimeout(120)
  }
  if (!poster) { await row.locator(sel.frame).waitFor({ timeout: 20_000 }); return row }   // autoplay may already have swapped the poster out
  await row.locator(sel.poster).waitFor({ timeout: 20_000 })
  await row.scrollIntoViewIfNeeded()
  await row.locator(sel.posterImg).waitFor({ state: 'attached', timeout: 10_000 }).catch(() => {})
  await page.waitForTimeout(500)
  return row
}

try {
  // ---------------------------------------------------------------- inline clip on the game page
  for (const width of WIDTHS) {
    const page = await open(width)
    // First navigation may wait for Metro to build the web bundle.
    await page.goto(`${BASE}/game/${league}/${gameId}`, { waitUntil: 'domcontentloaded', timeout: 240_000 })
    const row = await findRow(page, width)
    const shot = width === 1280 ? { before: `${shots}video-fix-expo-1280-before.png`, after: `${shots}video-fix-expo-1280-after.png` } : null
    const { before, after, playing } = await posterToVideo({ page, row, sel, check, label: `game @${width}`, shot, fitOf: m => m.posterImg?.objectFit })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    check(`game @${width} no horizontal overflow`, overflow <= 1, `${overflow}px`)
    table.push({ ui: ':8082 game', width, poster: round(before.poster), video: round(after.video), fit: after.video?.objectFit,
      metaBelow: after.meta.y >= after.frame.y + after.frame.h - 0.5, playing })
    if (width === 1280) table.push(...(await resizeSweep({ page, row, sel, check, label: 'game resize' }))
      .map(r => ({ ui: ':8082 game (resized while playing)', width: r.width, video: r.video })))
    await page.close()
  }

  // ---------------------------------------------------------------- Instagram-style autoplay on scroll
  for (const width of [390, 1280]) {
    const page = await open(width, 900, { autoplay: true })
    await page.goto(`${BASE}/game/${league}/${gameId}`, { waitUntil: 'domcontentloaded', timeout: 240_000 })
    const row = await findRow(page, width, { poster: false })
    const r = await autoplayOnScroll({ page, row, sel, check, label: `autoplay @${width}`,
      shot: width === 1280 ? `${shots}video-fix-expo-1280-autoplay.png` : null })
    autoplayRows.push({ ui: ':8082', width, ...r })
    await page.close()
  }

  // ---------------------------------------------------------------- Clips tab cards + full-screen player
  for (const width of WIDTHS) {
    const height = width === 390 ? 844 : 900
    const page = await open(width, height)
    await page.goto(`${BASE}/clips`, { waitUntil: 'domcontentloaded', timeout: 240_000 })
    const card = page.locator('[data-testid^="library-"][role="button"]').first()
    await card.locator('[data-testid="library-media"]').waitFor({ timeout: 120_000 })
    await page.waitForTimeout(800)
    const c = await measure(card, { frame: '[data-testid="library-media"]', img: '[data-testid="library-media"] img', meta: '[data-testid="library-meta"]' })
    if (width === 1280) await page.screenshot({ path: `${shots}video-fix-expo-clips-1280.png` })
    let label = `clips tab @${width}`
    check(`${label} card frame 16:9`, is169(c.frame), fmt(c.frame))
    check(`${label} card frame <= ${MAX_FRAME}px`, c.frame && c.frame.w <= MAX_FRAME + 1, fmt(c.frame))
    if (c.img) {
      check(`${label} poster fills frame`, sameBox(c.img, c.frame), `${fmt(c.img)} vs ${fmt(c.frame)}`)
      check(`${label} poster contain`, c.img.objectFit === 'contain', c.img.objectFit)
    }
    check(`${label} meta below frame`, c.meta && c.frame && c.meta.y >= c.frame.y + c.frame.h - 0.5)
    table.push({ ui: ':8082 clips tab card', width, poster: round(c.frame), fit: c.img?.objectFit })

    // Full-screen player: opened from the first library card that has a video file.
    const lib = await (await fetch(`${API}/api/highlights`)).json()
    const h = (Array.isArray(lib) ? lib : []).find(x => x.file)
    if (!h) { check(`${label} has a video to open`, false); await page.close(); continue }
    const q = new URLSearchParams({ url: `${API}/clips/${h.file}`, title: h.title ?? '', league: h.league ?? '', kind: 'official_upload' })
    await page.goto(`${BASE}/player?${q}`, { waitUntil: 'domcontentloaded', timeout: 240_000 })
    const screen = page.locator('body')
    await page.locator('[data-testid="player-media"] video').waitFor({ state: 'attached', timeout: 60_000 })
    await page.waitForTimeout(1200)
    const p = await measure(screen, { frame: '[data-testid="player-media"]', video: '[data-testid="player-media"] video', close: '[aria-label="Close"]' })
    const titleBottom = await page.getByText(h.title ?? '', { exact: true }).first().boundingBox().then(b => b && b.y + b.height).catch(() => null)
    if (width === 1280) await page.screenshot({ path: `${shots}video-fix-expo-player-1280.png` })
    label = `player @${width}`
    check(`${label} video fills frame`, sameBox(p.video, p.frame), `${fmt(p.video)} vs ${fmt(p.frame)}`)
    check(`${label} 16:9`, is169(p.frame), fmt(p.frame))
    check(`${label} contain`, p.video?.objectFit === 'contain', p.video?.objectFit)
    check(`${label} fits the screen`, p.frame && p.frame.absX >= -0.5 && p.frame.absX + p.frame.w <= width + 0.5 && p.frame.absY + p.frame.h <= height + 0.5, fmt(p.frame))
    check(`${label} title bar above the video`, p.close && p.frame && p.close.absY + p.close.h <= p.frame.absY + 0.5 && (titleBottom == null || titleBottom <= p.frame.absY + 0.5),
      `close bottom ${(p.close?.absY + p.close?.h).toFixed(1)}, title bottom ${titleBottom?.toFixed(1)}, frame top ${p.frame?.absY.toFixed(1)}`)
    check(`${label} native controls`, p.video?.controls === true)
    check(`${label} not fullscreen`, p._fullscreen === false)
    table.push({ ui: ':8082 full-screen player', width, video: round(p.video), fit: p.video?.objectFit })
    await page.close()
  }
  check('no page errors', pageErrors.length === 0, [...new Set(pageErrors)].join(' | '))
} catch (e) {
  failures.push(`fatal: ${e.stack ?? e.message}`)
} finally {
  await browser.close()
}

console.table(table.map(r => ({ ui: r.ui, width: r.width, poster: r.poster ? fmt(r.poster) : '-', video: r.video ? fmt(r.video) : '-',
  fit: r.fit ?? '', metaBelow: r.metaBelow ?? '', playing: r.playing ?? '' })))
console.table(autoplayRows.map(r => ({ ui: r.ui, width: r.width, startedOnScroll: r.started, muted: r.muted, playingAtOnce: r.playingCount,
  pausedOffscreen: r.paused, resumedOnReturn: r.resumed, video: fmt(r.video) })))
if (failures.length) {
  console.log(`\n${failures.length} failure(s):\n- ${failures.join('\n- ')}`)
  process.exit(1)
}
console.log(`\nOK: poster == video at ${WIDTHS.join('/')}px, 16:9, contain, meta below, one tap plays; resize sweep, Clips tab and player OK. Screenshots in ${shots}`)
