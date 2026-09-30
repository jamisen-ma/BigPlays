// Real-network acceptance test. Start the app and resolver; requires a live CFB listing.
import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'

const base = process.env.BIGPLAYS_TEST_BASE || 'http://127.0.0.1:8000'
const artifacts = new URL('../../data/validation/', import.meta.url)
await mkdir(artifacts, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } })
const failures = []
const mediaFailures = []
page.on('pageerror', error => failures.push(error.message))
page.on('response', response => {
  if (response.url().includes('/api/hls?') && response.status() >= 400) mediaFailures.push(response.status())
})
let ownedGame
try {
  const state = await (await page.request.get(base + '/api/recording')).json()
  assert.equal(state.recording, false, 'An existing recording is active; leave it untouched')
  const discovery = await (await page.request.get(base + '/api/live-streams')).json()
  assert.equal(discovery.ok, true)
  const matched = discovery.games.filter(game => game.league === 'ncaaf' && game.status === 'matched')
  const game = process.env.BIGPLAYS_TEST_GAME_ID
    ? matched.find(game => game.game_id === process.env.BIGPLAYS_TEST_GAME_ID)
    : matched.at(-1)
  assert.ok(game, 'No matching live CFB game is available for this test')
  console.log(`Testing discovered game: ${game.name} (${game.game_id})`)
  await page.goto(base + '/?view=clips')
  await page.getByRole('button', { name: 'CFB', exact: true }).click()
  const row = page.locator('.live-stream > div').filter({ hasText: `${game.name} · Stream found` })
  const resolving = page.waitForResponse(response => response.url() === base + '/api/stream'
    && response.request().method() === 'POST', { timeout: 65000 })
  await row.getByRole('button', { name: 'Watch & record', exact: true }).click()
  const resolved = await (await resolving).json()
  assert.equal(resolved.ok, true, `${resolved.stage}: ${resolved.error}`)
  assert.equal(resolved.recording, true)
  ownedGame = game.game_id
  assert.ok(resolved.proxiedUrl.startsWith('/api/hls?'))
  await page.locator('.live-stream video').scrollIntoViewIfNeeded()
  const recording = await (await page.request.get(base + '/api/recording')).json()
  assert.equal(recording.game.game_id, game.game_id)
  assert.equal(recording.game.league, 'ncaaf')
  await page.waitForFunction(() => {
    const video = document.querySelector('.live-stream video')
    return video && video.readyState >= 2 && video.currentTime > 2
  }, undefined, { timeout: 90000 })
  const before = await page.locator('.live-stream video').evaluate(video => video.currentTime)
  await page.waitForFunction(before => document.querySelector('.live-stream video')?.currentTime > before + 3,
    before, { timeout: 30000 })
  await page.locator('.live-stream video').screenshot({ path: new URL('cfb-live.png', artifacts).pathname })
  console.log('Live HLS playback advanced; recording is running.')
  // Optional observation window to wait through a broadcast commercial break.
  const observeSeconds = Number(process.env.BIGPLAYS_TEST_OBSERVE_SECONDS || 0)
  assert.ok(Number.isFinite(observeSeconds) && observeSeconds >= 0 && observeSeconds <= 300)
  for (let elapsed = 0; elapsed < observeSeconds; elapsed += 15) {
    await page.waitForTimeout(Math.min(15, observeSeconds - elapsed) * 1000)
    await page.locator('.live-stream video').screenshot({ path: new URL('cfb-live.png', artifacts).pathname })
  }

  let clip
  const deadline = Date.now() + 90000
  while (Date.now() < deadline) {
    const cutting = page.waitForResponse(response => response.url() === base + '/api/recording/clip'
      && response.request().method() === 'POST', { timeout: 65000 })
    await page.getByRole('button', { name: 'Manual cut: latest 14 seconds', exact: true }).click()
    clip = await (await cutting).json()
    if (clip.ok) break
    assert.match(clip.error, /buffer.*fill|allow more recording time/i)
    await page.waitForTimeout(5000)
  }
  assert.equal(clip?.ok, true, clip?.error)
  const meta = await (await page.request.get(base + clip.file.replace(/\.mp4$/, '.json'))).json()
  assert.equal(meta.game_id, game.game_id)
  assert.equal(meta.league, 'ncaaf')
  assert.equal(meta.name, game.name)
  // The CFB-filtered feed must receive this exact clip through the app's event stream.
  const filename = clip.file.split('/').at(-1)
  await page.waitForFunction(filename => document.querySelector('.player video')?.getAttribute('src') === `/clips/${filename}`,
    filename, { timeout: 15000 })
  await page.waitForFunction(() => document.querySelector('.player video')?.currentTime > 1,
    undefined, { timeout: 30000 })
  const clipDuration = await page.locator('.player video').evaluate(video => video.duration)
  assert.ok(clipDuration >= 10 && clipDuration <= 20,
    `Expected approximately 14 seconds of footage; received ${clipDuration}s`)
  await page.locator('.player video').screenshot({ path: new URL('cfb-highlight.png', artifacts).pathname })
  assert.deepEqual(failures, [])
  const result = { checked_at: new Date().toISOString(), game, live_cfb_games: discovery.games.filter(g => g.league === 'ncaaf').length,
    matched_cfb_games: matched.length, browser: 'Chrome', live_hls_playback: 'passed', recording: 'passed',
    clip: clip.file, clip_duration_seconds: clipDuration, clip_metadata: 'passed', highlight_playback: 'passed' }
  await writeFile(new URL('cfb-result.json', artifacts), JSON.stringify(result, null, 2) + '\n')
  await writeFile(new URL(`cfb-${meta.event_id}.json`, artifacts), JSON.stringify(result, null, 2) + '\n')
  console.log(JSON.stringify(result, null, 2))
} catch (error) {
  console.error('Live CFB test failed:', error.message)
  console.error('Visible errors:', await page.locator('[role="alert"]').allTextContents())
  console.error('Media diagnostics:', JSON.stringify(await page.locator('.live-stream video').evaluateAll(videos =>
    videos.map(v => ({ currentTime: v.currentTime, paused: v.paused, readyState: v.readyState,
      error: v.error?.message, buffered: Array.from({ length: v.buffered.length }, (_, i) => [v.buffered.start(i), v.buffered.end(i)]) })))), mediaFailures)
  await page.screenshot({ path: new URL('cfb-failure.png', artifacts).pathname, fullPage: true })
  process.exitCode = 1
} finally {
  if (ownedGame) {
    const state = await (await page.request.get(base + '/api/recording')).json()
    if (state.game?.game_id === ownedGame) await page.request.post(base + '/api/recording/stop')
  }
  await browser.close()
}
