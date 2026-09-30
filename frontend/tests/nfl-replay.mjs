import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const base = process.env.BIGPLAYS_TEST_BASE || 'http://127.0.0.1:8000'
const catalog = JSON.parse(await readFile('../bigplays/demo/data/nfl-2026-week3.json', 'utf8'))
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, timezoneId: 'America/Los_Angeles' })
const errors = []
page.on('pageerror', error => errors.push(error.message))
try {
  const status = await (await page.request.get(base + '/api/status')).json()
  assert.equal(status.mode, 'demo')
  assert.equal(status.demo_dataset, 'nfl-2026-week3')
  await page.goto(base + '/?view=clips')
  await page.getByText('NFL 2026 · WEEK 3 REPLAY', { exact: true }).waitFor()
  await page.getByLabel('auto-play new').uncheck()
  let highlights = []
  let previousCount = -1
  const deadline = Date.now() + 150000
  while (Date.now() < deadline) {
    highlights = await (await page.request.get(base + '/api/highlights')).json()
    if (highlights.length === catalog.length) break
    if (highlights.length !== previousCount) {
      console.log(`Week 3 feed: ${highlights.length}/${catalog.length} plays`)
      previousCount = highlights.length
    }
    await page.request.post(base + '/api/demo/next')
    await page.waitForTimeout(1200)
  }
  assert.equal(highlights.length, 15)
  const games = await (await page.request.get(base + '/api/games')).json()
  assert.equal(games.length, 6)
  assert.ok(games.every(g => g.league === 'nfl' && g.season === 2026 && g.week === 3))
  const latestArrival = highlights[0].received_utc
  await page.request.post(base + '/api/demo/next')
  await page.waitForFunction(previous => {
    return fetch('/api/highlights').then(r => r.json()).then(items => items[0].received_utc !== previous)
  }, latestArrival, { timeout: 25000 })
  // Reload exercises disk history and prevents stale SSE state from hiding missing records.
  await page.reload()
  await page.getByLabel('auto-play new').uncheck()
  const playback = []
  for (const expected of catalog) {
    const h = highlights.find(h => h.source_play_id === expected.source_play_id)
    assert.ok(h)
    assert.equal(h.occurred_utc, expected.occurred_utc)
    assert.equal(h.clock, expected.clock)
    assert.equal(h.period, expected.period)
    assert.equal(h.media_kind, 'broadcast')
    assert.notEqual(h.occurred_utc, h.received_utc)
    assert.equal(h.clip_duration, expected.video_end - expected.video_start)
    await page.locator('.feed-item:not(.pending)').filter({ hasText: h.title }).click()
    await page.waitForFunction(({ file, duration }) => {
      const video = document.querySelector('.player video')
      return video?.getAttribute('src') === '/clips/' + file && video.currentTime > .5
        && Math.abs(video.duration - duration) < .1
    }, { file: h.file, duration: h.clip_duration }, { timeout: 15000 })
    assert.equal(await page.locator('.player time').getAttribute('datetime'), expected.occurred_utc)
    assert.ok((await page.locator('.player time').textContent()).includes('PDT'))
    assert.equal(await page.getByRole('link', { name: 'ESPN play-by-play' }).getAttribute('href'), expected.play_by_play_url)
    // Confirm the tail is playable too, including long returns and the conversion clip.
    await page.locator('.player video').evaluate(video => { video.currentTime = video.duration - .8 })
    await page.waitForFunction(() => {
      const video = document.querySelector('.player video')
      return video?.readyState >= 2 && !video.seeking
    }, null, { timeout: 10000 })
    playback.push({ play: h.source_play_id, clock: `${h.period} ${h.clock}`, duration: h.clip_duration, footage: 'passed' })
  }
  const monday = catalog.find(h => h.source_play_id === '401872963315')
  await page.locator('.feed-item:not(.pending)').filter({ hasText: monday.title }).click()
  await page.getByText(/Original play:/).waitFor()
  const displayed = (await page.locator('.player time').textContent()).replace(/[\u202f\u00a0]/g, ' ')
  assert.equal(displayed, 'Sep 28, 2026, 5:23:47 PM PDT')
  await page.screenshot({ path: '../data/nfl-week3-dashboard.png', fullPage: true })
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ arrivals: 'passed', originalTimestamps: 'passed', timezone: displayed,
    nflGames: games.length, playback, errors }, null, 2))
} finally {
  await browser.close()
}
