// Video frame test for the dashboard served by the backend (default http://127.0.0.1:8000).
// Uses the running backend's real data: a game's play-by-play clip and the Clips library player.
//
// For each viewport width (390, 768, 1280, 1600) it opens the game, records the clip poster's box,
// clicks play, waits ~1s, records the <video> box, and asserts (see video-frame-helpers.mjs):
// poster == video (±1px), 16:9 (±1%), object-fit: contain, the "source · reason" row below the frame,
// frame <= 720px, native controls, no fullscreen, playback started by that one click, no page overflow. Then it resizes the window
// 320 -> 2000px while the clip plays, checks Instagram-style autoplay (plays muted when scrolled into
// view, one at a time, pauses off screen, resumes on return), and the Clips library player's frame.
//
// Usage: node tests/video-frame.mjs
//   BIGPLAYS_TEST_BASE=http://127.0.0.1:8000  GAME=mlb/401907896  PLAY_MATCH='Velázquez homered'
//   SHOTS_DIR=../mobile/screenshots (writes video-fix-web-*.png there)
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { WIDTHS, autoplayOnScroll, fmt, is169, makeChecker, measure, posterToVideo, resizeSweep, round, sameBox, MAX_FRAME } from './video-frame-helpers.mjs'

const BASE = (process.env.BIGPLAYS_TEST_BASE ?? process.env.BASE ?? 'http://127.0.0.1:8000').replace(/\/+$/, '')
const [league, gameId] = (process.env.GAME ?? 'mlb/401907896').split('/')
const PLAY_MATCH = new RegExp(process.env.PLAY_MATCH ?? 'Velázquez homered', 'i')
const shots = process.env.SHOTS_DIR ?? fileURLToPath(new URL('../../mobile/screenshots/', import.meta.url))
mkdirSync(shots, { recursive: true })

// Pick the play: the one matching PLAY_MATCH, else the first play whose clip has a video.
const detail = await (await fetch(`${BASE}/api/games/${league}/${gameId}`)).json()
const withVideo = (detail.plays ?? []).filter(p => p.clip?.video_url)
const play = withVideo.find(p => PLAY_MATCH.test(p.text)) ?? withVideo[0]
if (!play) { console.error(`no play with a playable clip in ${league}/${gameId}`); process.exit(1) }
console.log(`${detail.game.away.abbr}@${detail.game.home.abbr}: "${play.text}" (${play.play_id})`)

const sel = { frame: '.clip-media', poster: '.clip-poster', video: '.clip-media > video', meta: '.clip-meta',
  overlays: '.clip-media .play-btn, .clip-media .clip-duration' }
const { failures, check } = makeChecker('web ')
const table = []
const autoplayRows = []
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const pageErrors = []
const open = async (width, height = 900, { autoplay = false } = {}) => {
  const page = await browser.newPage({ viewport: { width, height } })
  page.on('pageerror', e => pageErrors.push(e.message))
  // Clips autoplay on scroll; the poster -> click checks need the poster, so opt out except in the autoplay section.
  if (!autoplay) await page.addInitScript(() => { window.__BIGPLAYS_AUTOPLAY__ = false })
  return page
}

try {
  for (const width of WIDTHS) {
    const page = await open(width)
    await page.goto(`${BASE}/?view=scores&league=${league}&game=${gameId}`)
    const row = page.locator(`[data-play-id="${play.play_id}"]`)
    await row.locator(sel.poster).waitFor({ timeout: 20_000 })
    await row.scrollIntoViewIfNeeded()
    await page.waitForTimeout(300)
    const shot = width === 1280 ? { before: `${shots}/video-fix-web-1280-before.png`, after: `${shots}/video-fix-web-1280-after.png` } : null
    const { before, after, playing } = await posterToVideo({ page, row, sel, check, label: `game @${width}`, shot, fitOf: m => m.poster?.backgroundSize })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    check(`game @${width} no horizontal overflow`, overflow <= 1, `${overflow}px`)
    table.push({ ui: ':8000 game', width, poster: round(before.poster), video: round(after.video), fit: after.video?.objectFit,
      metaBelow: after.meta.y >= after.frame.y + after.frame.h - 0.5, playing })
    if (width === 1280) table.push(...(await resizeSweep({ page, row, sel, check, label: 'game resize' }))
      .map(r => ({ ui: ':8000 game (resized while playing)', width: r.width, poster: null, video: r.video })))
    await page.close()
  }

  // Instagram-style autoplay: plays when scrolled into view, pauses when it leaves, resumes on return.
  for (const width of [390, 1280]) {
    const page = await open(width, 900, { autoplay: true })
    await page.goto(`${BASE}/?view=scores&league=${league}&game=${gameId}`)
    const row = page.locator(`[data-play-id="${play.play_id}"]`)
    await row.locator(sel.frame).waitFor({ timeout: 20_000 })
    const r = await autoplayOnScroll({ page, row, sel, check, label: `autoplay @${width}`,
      shot: width === 1280 ? `${shots}/video-fix-web-1280-autoplay.png` : null })
    autoplayRows.push({ ui: ':8000', width, ...r })
    await page.close()
  }

  // Clips library player: one 16:9 contain frame; badges + mute in the bar below it.
  for (const width of WIDTHS) {
    const page = await open(width)
    await page.goto(`${BASE}/?view=clips`)
    const wrap = page.locator('.video-wrap')
    await wrap.waitFor({ timeout: 20_000 })
    await wrap.locator('video, .clip-player').first().waitFor({ state: 'attached' })
    await page.waitForTimeout(1000)
    await wrap.scrollIntoViewIfNeeded()
    const m = await measure(wrap, { frame: '.video-frame', media: '.video-frame > video, .video-frame > .clip-player', badges: '.video-badges' })
    if (width === 1280) await wrap.screenshot({ path: `${shots}/video-fix-web-library-1280.png` })
    const label = `library @${width}`
    check(`${label} media fills frame`, sameBox(m.media, m.frame), `${fmt(m.media)} vs ${fmt(m.frame)}`)
    check(`${label} 16:9`, is169(m.frame), fmt(m.frame))
    check(`${label} <= ${MAX_FRAME}px`, m.frame && m.frame.w <= MAX_FRAME + 1, fmt(m.frame))
    if (m.media?.tag === 'video') check(`${label} object-fit contain`, m.media.objectFit === 'contain', m.media.objectFit)
    check(`${label} badges below frame`, m.badges && m.frame && m.badges.y >= m.frame.y + m.frame.h - 0.5)
    table.push({ ui: ':8000 library', width, poster: null, video: round(m.media), fit: m.media?.objectFit })
    await page.close()
  }
  check('no page errors', pageErrors.length === 0, pageErrors.join(' | '))
} catch (e) {
  failures.push(`fatal: ${e.stack ?? e.message}`)
} finally {
  await browser.close()
}

console.table(table.map(r => ({ ui: r.ui, width: r.width, poster: r.poster ? fmt(r.poster) : '-', video: fmt(r.video), fit: r.fit ?? '', metaBelow: r.metaBelow ?? '', playing: r.playing ?? '' })))
console.table(autoplayRows.map(r => ({ ui: r.ui, width: r.width, startedOnScroll: r.started, muted: r.muted, playingAtOnce: r.playingCount,
  pausedOffscreen: r.paused, resumedOnReturn: r.resumed, video: fmt(r.video) })))
if (failures.length) {
  console.log(`\n${failures.length} failure(s):\n- ${failures.join('\n- ')}`)
  process.exit(1)
}
console.log(`\nOK: poster == video at ${WIDTHS.join('/')}px, 16:9, contain, meta below; autoplay on scroll; resize sweep + library frame OK. Screenshots in ${shots}`)
