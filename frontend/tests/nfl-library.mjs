import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'

const base = process.env.BIGPLAYS_TEST_BASE || 'http://127.0.0.1:8000'
const expectedImports = Number(process.env.BIGPLAYS_EXPECTED_IMPORTS || 345)
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, timezoneId: 'America/Los_Angeles' })
const errors = []
page.on('pageerror', error => errors.push(error.message))
try {
  const highlights = await (await page.request.get(base + '/api/highlights')).json()
  const imported = highlights.filter(h => h.imported && h.replay_dataset === 'nfl-2026-week3')
  assert.ok(imported.length >= expectedImports, `Expected ${expectedImports} saved NFL imports, got ${imported.length}`)
  const games = [...new Set(imported.map(h => h.game_id))]
  assert.equal(games.length, 16)
  assert.equal(new Set(highlights.map(h => h.event_id)).size, highlights.length)
  const status = await (await page.request.get(base + '/api/status')).json()
  assert.equal(status.catalog.storage, 'sqlite')
  assert.equal(status.catalog.persistent, true)

  await page.goto(base + '/?view=clips')
  await page.getByRole('heading', { name: 'Highlight library' }).waitFor()
  await page.getByLabel('auto-play new').uncheck()
  // Initial SSE history must include the entire library, not just the newest 30 or 200.
  await page.waitForFunction(count => document.querySelectorAll('.feed-item:not(.pending)').length >= count, highlights.length)
  await page.getByRole('button', { name: 'NFL', exact: true }).click()
  assert.equal(await page.getByLabel('Filter by game').locator('option').count(), 17)

  const search = page.getByLabel('Search highlights')
  await search.fill('no-such-player-zzzz')
  await page.getByText('No highlights match these filters.').waitFor()
  assert.equal(await page.locator('.feed-item:not(.pending)').count(), 0)
  await search.fill('')

  const playback = []
  for (const game of games) {
    const expected = imported.find(h => h.game_id === game)
    await page.getByLabel('Filter by game').selectOption(game)
    await page.locator(`[data-event-id="${expected.event_id}"]`).click()
    const shown = await page.locator('.feed-item:not(.pending)').evaluateAll(items => items.map(item => item.dataset.eventId))
    assert.ok(shown.length > 0)
    assert.ok(shown.every(id => highlights.find(h => h.event_id === id)?.game_id === game))
    await page.waitForFunction(({ file, duration }) => {
      const video = document.querySelector('.player video')
      return video?.getAttribute('src') === '/clips/' + file && video.currentTime > .25
        && Math.abs(video.duration - duration) < .5
    }, { file: expected.file, duration: expected.clip_duration }, { timeout: 20000 })
    const eventTime = page.locator('.original-play-time')
    assert.equal(await eventTime.getAttribute('datetime'), expected.occurred_utc ?? null)
    if (expected.occurred_utc) assert.match(await eventTime.textContent(), /PDT/)
    else assert.equal(await eventTime.textContent(), 'Event time unavailable')
    await page.getByText(/Archived NFL highlight/).waitFor()
    assert.equal(await page.getByText('Reddit reactions', { exact: true }).count(), 0)
    assert.equal(await page.getByRole('link', { name: 'NFL', exact: true }).getAttribute('href'), expected.source.url)
    await page.locator('.player video').evaluate(video => { video.currentTime = Math.max(0, video.duration - 1) })
    await page.waitForFunction(() => {
      const video = document.querySelector('.player video')
      return video?.readyState >= 2 && !video.seeking
    }, null, { timeout: 10000 })
    playback.push({ game, event: expected.event_id, duration: expected.clip_duration })
  }
  await page.getByLabel('Filter by game').selectOption('all')
  const unresolved = imported.find(h => !h.occurred_utc)
  if (unresolved) {
    await page.locator(`[data-event-id="${unresolved.event_id}"]`).click()
    assert.equal(await page.locator('.original-play-time').textContent(), 'Event time unavailable')
    assert.equal(await page.locator('.original-play-time').getAttribute('datetime'), null)
    if (unresolved.published_utc) await page.getByText(/Published by NFL:/).waitFor()
  }
  const sample = imported[0]
  await search.fill(sample.title)
  await page.locator(`[data-event-id="${sample.event_id}"]`).waitFor()
  await page.reload()
  await page.getByLabel('auto-play new').uncheck()
  await page.waitForFunction(count => document.querySelectorAll('.feed-item:not(.pending)').length >= count, highlights.length)
  await page.locator(`[data-event-id="${sample.event_id}"]`).click()
  await page.screenshot({ path: '../data/nfl-week3-library.png', fullPage: true })
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ savedImports: imported.length, totalHighlights: highlights.length, games: games.length,
    fullHistory: 'passed', searchAndFilters: 'passed', browserReload: 'passed', playback, errors }, null, 2))
} finally {
  await browser.close()
}
