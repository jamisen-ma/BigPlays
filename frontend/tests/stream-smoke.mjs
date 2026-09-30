// Live network smoke test: start BigPlays first. Uses the installed Chrome browser.
import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } })
const failures = []
page.on('pageerror', error => failures.push(error.message))
try {
  const base = process.env.BIGPLAYS_TEST_BASE || 'http://127.0.0.1:8000'
  await page.goto(base + '/?view=clips')
  await page.getByRole('button', { name: 'CFB', exact: true }).click()
  const discovery = await (await page.request.get(base + '/api/live-streams')).json()
  assert.ok(discovery.ok)
  assert.ok(discovery.upcoming.some(game => game.league === 'ncaaf') || discovery.games.some(game => game.league === 'ncaaf'))
  await page.getByLabel('PPV event URL').fill(process.env.BIGPLAYS_TEST_EVENT || 'https://ppv.st/live/nfl-network')
  await page.getByLabel('Record for highlights').uncheck()
  await page.getByRole('button', { name: 'Load stream', exact: true }).click()
  await page.waitForFunction(() => {
    const video = document.querySelector('.live-stream video')
    return video && video.readyState >= 2 && video.currentTime > 2
  }, undefined, { timeout: 90000 })
  const before = await page.locator('.live-stream video').evaluate(video => video.currentTime)
  await page.waitForFunction(before => document.querySelector('.live-stream video')?.currentTime > before + 2, before,
    { timeout: 20000 })
  await page.getByRole('button', { name: 'ALL', exact: true }).click()
  const clip = page.locator('.feed-title').filter({ hasText: 'Live stream validation' }).first()
  if (await clip.count()) {
    await clip.click()
    await page.waitForFunction(() => document.querySelector('.player video')?.currentTime > 1, undefined, { timeout: 30000 })
  }
  await mkdir('../data/validation', { recursive: true })
  await page.screenshot({ path: '../data/validation/dashboard.png', fullPage: true })
  assert.deepEqual(failures, [])
  console.log(JSON.stringify({ browser: 'Chrome', cfb_discovery: 'passed', live_hls_playback: 'passed',
    highlight_playback: await clip.count() ? 'passed' : 'not present' }))
} catch (error) {
  console.error('Browser smoke test failed:', error.message)
  console.error('Visible errors:', await page.locator('[role="alert"]').allTextContents())
  process.exitCode = 1
} finally { await browser.close() }
