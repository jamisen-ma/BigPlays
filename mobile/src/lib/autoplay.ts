// Instagram-style inline autoplay for play-by-play clips (ported from frontend/src/games/autoplay.ts).
// A clip plays while it's on screen and pauses once it scrolls away. One clip plays at a time: the
// topmost one that's >= 60% visible; it keeps playing until it drops below 25% visible, then the
// next visible clip takes over. A clip the viewer started keeps playing while any of it is visible.
//
// Visibility comes from:
//  - web: an IntersectionObserver on each clip's media frame (setRatio). The SectionList's own
//    viewability is unreliable on react-native-web: cells are only re-measured when they resize,
//    not when they move, so it reports the wrong rows once rows above render in;
//  - native: the game screen's SectionList viewability callbacks (setVisible).
// Autoplay starts muted unless the viewer turned sound on (tapped a poster or unmuted a clip).
// Opt out: reduce-motion, or (web) window.__BIGPLAYS_AUTOPLAY__ = false for tests of the tap flow.
import { AccessibilityInfo, Platform } from 'react-native'

export const START = 0.6
export const KEEP = 0.25
export const ANY = 0.01

type Entry = { ratio: number; done: boolean; top: () => number; play: () => void; pause: () => void }
const entries = new Map<string, Entry>()
let current: string | null = null
/** A clip the viewer started: it keeps playing while any of it is visible. */
let pinned: string | null = null
let soundOn = false
let reduceMotion = false
AccessibilityInfo.isReduceMotionEnabled?.().then(v => { reduceMotion = v }).catch(() => {})

export const usesIntersectionObserver = Platform.OS === 'web' && typeof IntersectionObserver !== 'undefined'

export function autoplayEnabled(): boolean {
  if (reduceMotion) return false
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    if ((window as unknown as { __BIGPLAYS_AUTOPLAY__?: boolean }).__BIGPLAYS_AUTOPLAY__ === false) return false
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return false
  }
  return true
}

function reconcile() {
  const cur = current ? entries.get(current) : undefined
  if (cur && !cur.done && cur.ratio >= (current === pinned ? ANY : KEEP)) return
  if (current === pinned) pinned = null
  const next = [...entries].filter(([, e]) => !e.done && e.ratio >= START)
    .sort(([, a], [, b]) => a.top() - b.top())[0]?.[0] ?? null
  if (next === current) return
  cur?.pause()
  current = next
  if (next) entries.get(next)!.play()
}

// ---- native: rows reported by the SectionList (ids top to bottom at each threshold)
const visible = { start: new Set<string>(), keep: new Set<string>(), any: [] as string[] }
const nativeRatio = (id: string) => visible.start.has(id) ? START : visible.keep.has(id) ? KEEP : visible.any.includes(id) ? ANY : 0

export function setVisible(level: 'start' | 'keep' | 'any', ids: string[]) {
  if (level === 'any') visible.any = ids
  else visible[level] = new Set(ids)
  if (usesIntersectionObserver) return
  for (const [id, e] of entries) e.ratio = nativeRatio(id)
  reconcile()
}

// ---- both
/** Register a clip; `top` orders candidates (web: the frame's screen position). Returns unregister. */
export function register(id: string, entry: { play: () => void; pause: () => void; top?: () => number }) {
  if (!autoplayEnabled()) return () => {}
  const e: Entry = {
    ...entry, done: false,
    ratio: usesIntersectionObserver ? 0 : nativeRatio(id),
    top: entry.top ?? (() => visible.any.indexOf(id)),
  }
  entries.set(id, e)
  reconcile()
  return () => {
    if (entries.get(id) === e) entries.delete(id)
    if (current === id) { current = null; if (pinned === id) pinned = null; reconcile() }
  }
}

/** Web: how much of the clip's frame is on screen (0-1). */
export function setRatio(id: string, ratio: number) {
  const e = entries.get(id)
  if (!e) return
  e.ratio = ratio
  reconcile()
}

/** The viewer started this clip (tap on the poster or the native ▶): it becomes the one playing. */
export function claim(id: string | undefined, withSound = false) {
  if (withSound) soundOn = true
  if (!id) return
  const e = entries.get(id)
  if (e) e.done = false
  if (current && current !== id) entries.get(current)?.pause()
  current = pinned = id
}

/** Finished clips aren't restarted; the next visible one can take over. */
export function ended(id: string | undefined) {
  if (!id) return
  const e = entries.get(id)
  if (e) e.done = true
  if (current === id) { current = null; if (pinned === id) pinned = null; reconcile() }
}

/** Start muted unless the viewer turned sound on (and, on web, the page may play sound). */
export function shouldMute(): boolean {
  if (!soundOn) return true
  if (Platform.OS !== 'web') return false
  const ua = (globalThis.navigator as Navigator & { userActivation?: { hasBeenActive: boolean } } | undefined)?.userActivation
  return ua ? !ua.hasBeenActive : false
}

export function setSoundOn(on: boolean) { soundOn = on }

/** Web: true right after a real click/tap (to tell the viewer's actions from programmatic ones). */
export function userGesture(): boolean {
  const ua = (globalThis.navigator as Navigator & { userActivation?: { isActive: boolean } } | undefined)?.userActivation
  return !!ua?.isActive
}
