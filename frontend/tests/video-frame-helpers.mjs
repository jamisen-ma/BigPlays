// Shared checks for the "one 16:9 media frame" contract (tests/video-frame.mjs for the dashboard,
// ../mobile/tests/video-frame.mjs for the Expo web app):
//  - poster and playing <video> fill the same 16:9 frame (±1px), so pressing play shifts nothing;
//  - the video (and poster) letterbox with object-fit: contain, so nothing is zoomed or cropped;
//  - the badge / "source · reason" row sits below the frame, never over the video;
//  - the frame is capped at ~720px and keeps 16:9 while the window is resized (320-2000px);
//  - one click on the poster starts playback (no second press of the native ▶).
export const WIDTHS = [390, 768, 1280, 1600]
export const RESIZE_WIDTHS = [320, 390, 600, 768, 1024, 1280, 1600, 2000]
export const MAX_FRAME = 720

export const round = b => b && ({ x: +b.x.toFixed(1), y: +b.y.toFixed(1), w: +b.w.toFixed(1), h: +b.h.toFixed(1) })
export const fmt = b => (b ? `${b.w.toFixed(1)}x${b.h.toFixed(1)} @${b.x.toFixed(1)},${b.y.toFixed(1)}` : 'missing')
export const sameBox = (a, b, tol = 1) => !!a && !!b && ['x', 'y', 'w', 'h'].every(k => Math.abs(a[k] - b[k]) <= tol)
export const is169 = b => !!b && b.h > 0 && Math.abs(b.w / b.h - 16 / 9) / (16 / 9) <= 0.01

/**
 * Boxes (relative to the `scope` element's top-left, so page/list scrolling doesn't matter) and a few
 * computed styles for each named selector inside it. `sels` values are CSS selectors; '' = scope itself.
 */
export function measure(scope, sels) {
  return scope.evaluate((root, sels) => {
    const r0 = root.getBoundingClientRect()
    const out = { _viewport: { w: window.innerWidth, h: window.innerHeight }, _scope: { x: r0.x, y: r0.y } }
    for (const [key, sel] of Object.entries(sels)) {
      const el = sel ? root.querySelector(sel) : root
      if (!el) { out[key] = null; continue }
      const r = el.getBoundingClientRect(), cs = getComputedStyle(el)
      out[key] = { x: r.x - r0.x, y: r.y - r0.y, w: r.width, h: r.height, absX: r.x, absY: r.y,
        objectFit: cs.objectFit, backgroundSize: cs.backgroundSize, tag: el.tagName.toLowerCase(),
        controls: el.tagName === 'VIDEO' ? el.controls : undefined }
    }
    out._fullscreen = !!document.fullscreenElement
    return out
  }, sels)
}

/** Collects failures instead of stopping at the first one, so a run reports everything. */
export function makeChecker(prefix = '') {
  const failures = []
  const check = (label, ok, detail = '') => { if (!ok) failures.push(`${prefix}${label}${detail ? `: ${detail}` : ''}`) }
  return { failures, check }
}

/**
 * The poster -> play flow on one clip card. `row` is a locator for the element that contains the
 * card (a play row); `sel` names the frame / poster / video / meta row (and optional posterImg,
 * overlays that must vanish on play). `fitOf(poster measurement)` returns the poster's fit value.
 * Returns the measurements; failures go to `check`.
 */
export async function posterToVideo({ page, row, sel, check, label, shot, fitOf = m => m.poster?.objectFit }) {
  const before = await measure(row, { frame: sel.frame, poster: sel.poster, meta: sel.meta, ...(sel.posterImg ? { posterImg: sel.posterImg } : {}) })
  if (shot) await page.screenshot({ path: shot.before })
  check(`${label} poster present`, !!before.poster && !!before.frame)
  check(`${label} poster fills frame`, sameBox(before.poster, before.frame), `${fmt(before.poster)} vs frame ${fmt(before.frame)}`)
  check(`${label} poster 16:9`, is169(before.poster), fmt(before.poster))
  check(`${label} poster letterboxed (contain)`, fitOf(before) === 'contain', String(fitOf(before)))
  check(`${label} frame <= ${MAX_FRAME}px wide`, before.frame && before.frame.w <= MAX_FRAME + 1, fmt(before.frame))
  check(`${label} meta row below poster`, before.meta && before.frame && before.meta.y >= before.frame.y + before.frame.h - 0.5,
    `meta top ${before.meta?.y.toFixed(1)} < frame bottom ${(before.frame?.y + before.frame?.h).toFixed(1)}`)

  await row.locator(sel.poster).first().click()
  await row.locator(sel.video).first().waitFor({ state: 'attached', timeout: 15_000 })
  const handle = await row.locator(sel.video).first().elementHandle()
  await page.waitForFunction(v => v && v.readyState >= 1, handle, { timeout: 15_000 }).catch(() => {})
  await page.waitForTimeout(1000)
  const playing = await page.waitForFunction(v => !v.paused && v.currentTime > 0, handle, { timeout: 8000 }).then(() => true, () => false)
  const after = await measure(row, { frame: sel.frame, video: sel.video, meta: sel.meta })
  const overlays = sel.overlays ? await row.locator(sel.overlays).count() : 0
  if (shot) await page.screenshot({ path: shot.after })

  check(`${label} video box == poster box (±1px)`, sameBox(after.video, before.poster), `video ${fmt(after.video)} vs poster ${fmt(before.poster)}`)
  check(`${label} no layout shift on play`, sameBox(after.frame, before.frame), `frame ${fmt(before.frame)} -> ${fmt(after.frame)}`)
  check(`${label} video 16:9`, is169(after.video), fmt(after.video))
  check(`${label} video object-fit contain`, after.video?.objectFit === 'contain', String(after.video?.objectFit))
  check(`${label} meta row below video`, after.meta && after.frame && after.meta.y >= after.frame.y + after.frame.h - 0.5,
    `meta top ${after.meta?.y.toFixed(1)} < frame bottom ${(after.frame?.y + after.frame?.h).toFixed(1)}`)
  check(`${label} native controls`, after.video?.controls === true)
  check(`${label} not fullscreen`, after._fullscreen === false)
  check(`${label} poster overlays gone while playing`, overlays === 0, `${overlays} left`)
  check(`${label} one click starts playback`, playing, 'video still paused after the poster click')
  return { before, after, playing }
}

/** While a clip plays, sweep the viewport width and check the frame keeps 16:9, stays filled, fits, and stays above the meta row. */
export async function resizeSweep({ page, row, sel, check, label, height = 900 }) {
  const rows = []
  for (const width of RESIZE_WIDTHS) {
    await page.setViewportSize({ width, height })
    await page.waitForTimeout(350)
    const m = await measure(row, { frame: sel.frame, video: sel.video, meta: sel.meta })
    rows.push({ width, frame: round(m.frame), video: round(m.video) })
    check(`${label} @${width} video present`, !!m.video)
    if (!m.video || !m.frame) continue
    check(`${label} @${width} video fills frame`, sameBox(m.video, m.frame), `${fmt(m.video)} vs ${fmt(m.frame)}`)
    check(`${label} @${width} 16:9`, is169(m.video), fmt(m.video))
    check(`${label} @${width} <= ${MAX_FRAME}px`, m.video.w <= MAX_FRAME + 1, fmt(m.video))
    check(`${label} @${width} on-screen horizontally`, m.video.absX >= -0.5 && m.video.absX + m.video.w <= width + 0.5, `x ${m.video.absX.toFixed(1)} w ${m.video.w.toFixed(1)}`)
    check(`${label} @${width} object-fit contain`, m.video.objectFit === 'contain', m.video.objectFit)
    check(`${label} @${width} meta below`, !m.meta || m.meta.y >= m.frame.y + m.frame.h - 0.5)
  }
  return rows
}

/**
 * Instagram-style autoplay: scroll the clip's frame into view (no click) -> it plays, muted, in the
 * same 16:9 contain frame, and it's the only video playing; scroll it away -> paused; back -> resumes.
 * `row` contains the clip; the page must NOT set window.__BIGPLAYS_AUTOPLAY__ = false.
 */
export async function autoplayOnScroll({ page, row, sel, check, label, shot }) {
  // Re-query through the locator each time: on Expo web the list may unmount/remount the row.
  const frame = row.locator(sel.frame).first()
  const until = async (fn, arg, timeout) => {
    for (const end = Date.now() + timeout; Date.now() < end; await page.waitForTimeout(100)) {
      if (await frame.evaluate(fn, arg).catch(() => false)) return true
    }
    return false
  }
  const videoState = () => frame.evaluate(el => { const v = el.querySelector('video'); return v && { paused: v.paused, muted: v.muted, t: v.currentTime } }).catch(() => null)
  await frame.evaluate(el => el.scrollIntoView({ block: 'center' }))
  const started = await until(el => { const v = el.querySelector('video'); return v && !v.paused && v.currentTime > 0.2 }, null, 12_000)
  check(`${label} autoplays when scrolled into view (no click)`, started)
  const m = await measure(row, { frame: sel.frame, video: sel.video, meta: sel.meta })
  const st = await videoState()
  const playingCount = await page.evaluate(() => [...document.querySelectorAll('video')].filter(v => !v.paused).length)
  if (shot) await page.screenshot({ path: shot })
  check(`${label} autoplay starts muted`, st?.muted === true)
  check(`${label} one clip playing at a time`, playingCount === 1, `${playingCount} playing`)
  check(`${label} autoplay video fills the 16:9 frame`, sameBox(m.video, m.frame) && is169(m.video), `${fmt(m.video)} vs ${fmt(m.frame)}`)
  check(`${label} autoplay video contain`, m.video?.objectFit === 'contain', String(m.video?.objectFit))
  check(`${label} meta row below autoplaying video`, m.meta && m.frame && m.meta.y >= m.frame.y + m.frame.h - 0.5)

  // Scroll it off screen: whichever ancestor scrolls (window on the dashboard, the list on Expo web).
  await frame.evaluate(el => {
    let p = el.parentElement
    while (p && !(p.scrollHeight > p.clientHeight + 10 && /(auto|scroll)/.test(getComputedStyle(p).overflowY))) p = p.parentElement
    window.__vfScroller = p ?? document.scrollingElement
    window.__vfScroller.scrollBy(0, 2500)
  })
  await page.waitForTimeout(300)
  const offscreen = await frame.evaluate(el => { const r = el.getBoundingClientRect(); return r.bottom <= 0 || r.top >= window.innerHeight }).catch(() => true)
  // Off screen = its video paused (or the list unmounted the row); and nothing plays that isn't on screen.
  const paused = await until(el => !el.querySelector('video') || el.querySelector('video').paused, null, 5000)
    || !(await frame.count())
  check(`${label} scrolled off screen`, offscreen)
  check(`${label} pauses once it leaves the screen`, paused)

  await page.evaluate(() => window.__vfScroller.scrollBy(0, -2500))
  await frame.waitFor({ state: 'attached', timeout: 10_000 })
  await frame.evaluate(el => el.scrollIntoView({ block: 'center' }))
  const t0 = (await videoState())?.t ?? 0
  const resumed = await until((el, t0) => { const v = el.querySelector('video'); return v && !v.paused && v.currentTime > Math.min(t0, 0.3) + 0.2 }, t0, 10_000)
  check(`${label} resumes when scrolled back`, resumed)
  return { started, muted: st?.muted, playingCount, paused, resumed, video: round(m.video) }
}
