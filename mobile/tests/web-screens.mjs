// Expo web smoke test + iPhone-size screenshots (390x844) of Scores, two game pages, Clips, Settings.
//
// 1. Start a web Metro on its own port (leave the phone's Metro on :8081 alone):
//      cd mobile && EXPO_PUBLIC_API_URL=http://127.0.0.1:8000 npx expo start --web --port 8082
// 2. node tests/web-screens.mjs
//      BASE=http://localhost:8082  API=http://127.0.0.1:8000  GAMES=mlb/401907974,mlb/401907965
//
// Writes screenshots/*.png. Exits 1 on page/console errors, HTTP >= 400, horizontal overflow, an
// inline clip that doesn't get a <video>, or a clip/poster URL that doesn't return 200/206.
// Uses Playwright from ../frontend/node_modules with the installed Google Chrome.
import { mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../../frontend/package.json', import.meta.url))
const { chromium } = require('@playwright/test')

const BASE = (process.env.BASE ?? 'http://localhost:8082').replace(/\/+$/, '')
const API = (process.env.API ?? 'http://127.0.0.1:8000').replace(/\/+$/, '')
const GAMES = (process.env.GAMES ?? 'mlb/401907974,mlb/401907965').split(',').filter(Boolean)
const outDir = fileURLToPath(new URL('../screenshots/', import.meta.url))
mkdirSync(outDir, { recursive: true })

const problems = []
const abs = u => (/^https?:/.test(u) ? u : `${API}${u.startsWith('/') ? '' : '/'}${u}`)

async function checkUrl(u, label) {
  try {
    const r = await fetch(abs(u), { headers: { Range: 'bytes=0-1023' } })
    await r.body?.cancel()
    if (r.status !== 200 && r.status !== 206) problems.push(`${label}: ${r.status} ${u}`)
    return r.status
  } catch (e) {
    problems.push(`${label}: ${e.message} ${u}`)
    return 0
  }
}

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
page.on('pageerror', e => problems.push(`pageerror: ${e.message}`))
page.on('console', m => { if (m.type() === 'error') problems.push(`console: ${m.text().slice(0, 300)}`) })
page.on('response', r => { if (r.status() >= 400 && !/favicon/.test(r.url())) problems.push(`HTTP ${r.status()} ${r.url()}`) })

async function shot(path, name, ready) {
  // The first navigation waits for Metro to build the web bundle.
  await page.goto(BASE + path, { waitUntil: 'domcontentloaded', timeout: 240_000 })
  await page.getByText(ready).first().waitFor({ timeout: 120_000 })
  await page.waitForTimeout(1500) // logos / posters
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  if (overflow > 1) problems.push(`${name}: horizontal overflow ${overflow}px`)
  await page.screenshot({ path: `${outDir}${name}.png` })
  console.log(`saved screenshots/${name}.png`)
}

try {
  await shot('/', 'scores-mlb-today', /\d+ games?|No MLB games/)

  for (const g of GAMES) {
    const [league, id] = g.split('/')
    await shot(`/game/${league}/${id}`, `game-${league}-${id}`, 'Play-by-play')

    // Every clip / poster / alternate the page can show must be fetchable.
    const body = await (await fetch(`${API}/api/games/${league}/${id}`)).json()
    const urls = new Set()
    for (const p of body.plays ?? []) {
      for (const c of [p.clip, ...(p.alternate_clips ?? [])].filter(Boolean)) { urls.add(c.video_url); urls.add(c.poster_url) }
    }
    for (const c of body.clips_unmatched ?? []) { urls.add(c.video_url); urls.add(c.poster_url) }
    const list = [...urls].filter(Boolean)
    const codes = await Promise.all(list.map(u => checkUrl(u, `${g} clip`)))
    console.log(`${g}: ${list.length} clip/poster URLs, ${codes.filter(c => c === 200 || c === 206).length} OK`)

    // Expand the first inline clip and make sure a <video> with a working src appears.
    const poster = page.locator('[aria-label^="Play clip"]').first()
    if (await poster.count()) {
      await poster.scrollIntoViewIfNeeded()
      await poster.click()
      const video = page.locator('video').first()
      await video.waitFor({ state: 'attached', timeout: 15_000 }).catch(() => problems.push(`${g}: no <video> after tapping a clip`))
      const src = await video.evaluate(v => v.currentSrc || v.src || v.querySelector('source')?.src || '').catch(() => '')
      if (src) console.log(`${g}: inline video ${src} -> ${await checkUrl(src, `${g} inline video`)}`)
      else problems.push(`${g}: <video> has no src`)
    }
  }

  await shot('/clips', 'clips', /\d+ clips/)
  const lib = await (await fetch(`${API}/api/highlights`)).json()
  const files = (Array.isArray(lib) ? lib : []).filter(h => h.file).slice(0, 15)
  await Promise.all(files.map(h => checkUrl(`/clips/${h.file}`, 'library video')))
  console.log(`library: checked ${files.length} newest video files`)

  await shot('/settings', 'settings', /Connected ·|Can't reach|failed/)
} catch (e) {
  problems.push(`fatal: ${e.message}`)
  await page.screenshot({ path: `${outDir}failure.png` }).catch(() => {})
} finally {
  await browser.close()
}

if (problems.length) {
  console.log(`\n${problems.length} problem(s):\n- ${[...new Set(problems)].join('\n- ')}`)
  process.exit(1)
}
console.log('\nOK: no page errors, no overflow, all clip URLs 200/206')
