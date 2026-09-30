import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'

const base = process.env.BIGPLAYS_TEST_BASE || 'http://127.0.0.1:8000'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, timezoneId: 'America/Los_Angeles' })
const errors = []
page.on('pageerror', error => errors.push(error.message))
try {
  const highlights = await (await page.request.get(base + '/api/highlights')).json()
  const baseball = highlights.filter(h => h.league === 'mlb' && h.imported)
  const football = highlights.filter(h => h.league === 'nfl')
  assert.ok(baseball.length > 0, 'Import official MLB highlights before running this test')
  assert.ok(football.length >= 345, 'Previously saved NFL highlights remain accessible')
  await page.goto(base + '/?view=clips&league=mlb')
  await page.getByRole('heading', { name: 'Highlight library' }).waitFor()
  await page.getByText('MLB · TODAY', { exact: true }).waitFor()
  await page.locator('.conn').filter({ hasText: 'CONNECTED' }).waitFor()
  const schedule = await (await page.request.get(base + '/api/mlb/games')).json()
  assert.ok(schedule.enabled)
  await page.getByRole('region', { name: 'MLB games today' }).waitFor()
  assert.equal(await page.locator('.mlb-game-card').count(), schedule.games.length)
  assert.equal(await page.getByLabel('auto-play new').isChecked(), false)
  await page.getByText(/Archived MLB highlight/).waitFor()
  await page.getByRole('button', { name: 'MLB', exact: true }).click()
  await page.waitForFunction(count => document.querySelectorAll('.feed-item:not(.pending)').length >= count, baseball.length)
  assert.equal(await page.locator('.feed-item .league-pill:not(.mlb)').count(), 0)
  const playback = []
  for (const game of new Set(baseball.map(h => h.game_id))) {
    const expected = baseball.find(h => h.game_id === game && h.occurred_utc) || baseball.find(h => h.game_id === game)
    await page.getByLabel('Filter by game').selectOption(game)
    await page.locator(`[data-event-id="${expected.event_id}"]`).click()
    await page.waitForFunction(file => {
      const video = document.querySelector('.player video')
      return video?.getAttribute('src') === '/clips/' + file && video.currentTime > .3 && video.readyState >= 2
    }, expected.file, { timeout: 20000 })
    assert.match(await page.locator('.player .live-pill').textContent(), /REPLAY/)
    await page.getByText(/Archived MLB highlight/).waitFor()
    assert.equal(await page.getByText(/Archived NFL highlight/).count(), 0)
    assert.equal(await page.locator('.original-play-time').getAttribute('datetime'), expected.occurred_utc ?? null)
    if (expected.inning) {
      assert.match(await page.locator('.player .clock').textContent(), new RegExp(`${expected.inning_half === 'top' ? 'Top' : 'Bottom'} ${expected.inning}`))
      assert.doesNotMatch(await page.locator('.player .clock').textContent(), /Q\d|0:00/)
    }
    if (expected.count_context) assert.ok((await page.locator('.player .clock').textContent()).includes(expected.count_context))
    assert.equal(await page.getByRole('link', { name: 'MLB', exact: true }).getAttribute('href'), expected.source.url)
    playback.push({ game, event: expected.event_id, duration: expected.clip_duration })
  }
  await page.getByRole('button', { name: 'NFL', exact: true }).click()
  await page.waitForFunction(count => document.querySelectorAll('.feed-item:not(.pending)').length >= count, football.length)
  assert.equal(await page.locator('.feed-item .league-pill:not(.nfl)').count(), 0)
  await page.getByRole('button', { name: 'ALL', exact: true }).click()
  await page.waitForFunction(count => document.querySelectorAll('.feed-item:not(.pending)').length >= count, highlights.length)
  await page.reload()
  await page.getByRole('button', { name: 'MLB', exact: true }).click()
  await page.waitForFunction(count => document.querySelectorAll('.feed-item:not(.pending)').length >= count, baseball.length)
  await page.locator(`[data-event-id="${baseball[0].event_id}"]`).click()
  await page.screenshot({ path: '../data/mlb-library.png', fullPage: true })
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ mlbHighlights: baseball.length, nflHighlightsRetained: football.length, leagueFilters: 'passed',
    sourceTimes: 'passed', reload: 'passed', playback, errors }, null, 2))
} finally {
  await browser.close()
}
