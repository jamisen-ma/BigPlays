// Start the persistent agent and wait for at least one clock-aligned play clip.
import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'

const base = process.env.BIGPLAYS_TEST_BASE || 'http://127.0.0.1:8000'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
try {
  const state = await (await page.request.get(base + '/api/agent')).json()
  assert.ok(state.running && state.enabled)
  assert.ok(state.games.some(game => game.segments > 0 && game.clock_observations > 0))
  const highlights = await (await page.request.get(base + '/api/highlights')).json()
  const clip = highlights.find(clip => clip.alignment && (!process.env.BIGPLAYS_TEST_PLAY_ID || clip.play_id === process.env.BIGPLAYS_TEST_PLAY_ID))
  assert.ok(clip, 'No clock-aligned play clip is available yet')
  await page.goto(base + '/?view=clips')
  await page.getByRole('heading', { name: 'Background play monitor', exact: true }).waitFor()
  await page.getByRole('button', { name: 'CFB', exact: true }).click()
  await page.locator('.feed-title').filter({ hasText: clip.title }).first().click()
  await page.locator('.player video').scrollIntoViewIfNeeded()
  await page.waitForFunction(file => {
    const video = document.querySelector('.player video')
    return video?.getAttribute('src') === '/clips/' + file && video.currentTime > 2
  }, clip.file, { timeout: 30000 })
  const later = await (await page.request.get(base + '/api/agent')).json()
  assert.ok(later.running)
  assert.notEqual(later.heartbeat, state.heartbeat)
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ agent: 'running independently', clip: clip.file, play_id: clip.play_id,
    game_clock: clip.clock, alignment: clip.alignment, browser_playback: 'passed' }, null, 2))
} finally { await browser.close() }
