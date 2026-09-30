// Instagram-style inline autoplay for clip cards: a clip plays while it's on screen and pauses once
// it scrolls away. One clip plays at a time: the topmost one that's at least START visible. It keeps
// playing until it drops below KEEP visible, then the next visible clip takes over.
// Autoplay starts muted (browsers block sound without a tap) unless the viewer has turned sound on
// (clicked a poster or unmuted a clip), like Instagram remembering your sound choice.
// Opt out: prefers-reduced-motion, or window.__BIGPLAYS_AUTOPLAY__ = false (tests of the manual flow).
import { useEffect, useRef, type RefObject } from 'react'

const START = 0.6
const KEEP = 0.25

type Entry = { el: HTMLElement; ratio: number; done: boolean; play: () => void; pause: () => void }
const entries = new Map<string, Entry>()
let current: string | null = null
/** A clip the viewer started themselves: it keeps playing while any of it is visible. */
let pinned: string | null = null
let soundOn = false

export function autoplayEnabled(): boolean {
  if (typeof window === 'undefined' || typeof IntersectionObserver === 'undefined') return false
  if ((window as unknown as { __BIGPLAYS_AUTOPLAY__?: boolean }).__BIGPLAYS_AUTOPLAY__ === false) return false
  return !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

function reconcile() {
  const cur = current ? entries.get(current) : undefined
  if (cur && !cur.done && cur.ratio >= (current === pinned ? 0.01 : KEEP)) return
  if (current === pinned) pinned = null
  const next = [...entries].filter(([, e]) => !e.done && e.ratio >= START)
    .sort(([, a], [, b]) => a.el.getBoundingClientRect().top - b.el.getBoundingClientRect().top)[0]?.[0] ?? null
  if (next === current) return
  cur?.pause()
  current = next
  if (next) entries.get(next)!.play()
}

/** The viewer started this clip (poster click or native ▶): it becomes the one playing. */
export function claim(id: string, withSound = false) {
  if (withSound) soundOn = true
  const e = entries.get(id)
  if (e) e.done = false
  if (current && current !== id) entries.get(current)?.pause()
  current = pinned = id
}

/** Play `v`, muted unless the viewer turned sound on and the page may play sound. */
export function startVideo(v: HTMLVideoElement | null) {
  if (!v || v.ended) return
  const mayPlaySound = soundOn && (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation?.hasBeenActive !== false
  v.muted = !mayPlaySound
  v.play().catch(() => {
    if (v.muted) return
    v.muted = true   // sound blocked: fall back to muted autoplay
    v.play().catch(() => { /* controls remain */ })
  })
}

/** Remember the viewer's mute choice from the native controls. */
export function noteVolume(v: HTMLVideoElement) {
  if (!v.paused && (navigator as Navigator & { userActivation?: { isActive: boolean } }).userActivation?.isActive) soundOn = !v.muted
}

/**
 * Register a clip card's media frame with the autoplay coordinator. `mount()` swaps the poster for
 * the <video>; `wantPlay` tells the card to start it once mounted. Returns `ended()` for the
 * video's `ended` event so a finished clip isn't restarted and the next visible one can take over.
 */
export function useAutoplay(id: string, frame: RefObject<HTMLElement | null>, video: RefObject<HTMLVideoElement | null>,
  enabled: boolean, mount: () => void) {
  const wantPlay = useRef(false)
  const mountRef = useRef(mount)
  mountRef.current = mount
  useEffect(() => {
    const el = frame.current
    if (!enabled || !el || !autoplayEnabled()) return
    const entry: Entry = {
      el, ratio: 0, done: false,
      play: () => { wantPlay.current = true; mountRef.current(); startVideo(video.current) },
      pause: () => { wantPlay.current = false; video.current?.pause() },
    }
    entries.set(id, entry)
    const io = new IntersectionObserver(([e]) => { entry.ratio = e.intersectionRatio; reconcile() },
      { threshold: [0, 0.01, KEEP, 0.5, START, 0.8, 1] })
    io.observe(el)
    return () => {
      io.disconnect()
      if (entries.get(id) === entry) entries.delete(id)
      if (current === id) { current = null; if (pinned === id) pinned = null; video.current?.pause(); reconcile() }
    }
  }, [id, enabled, frame, video])
  const ended = () => {
    const e = entries.get(id)
    if (e) e.done = true
    if (current === id) { current = null; if (pinned === id) pinned = null; reconcile() }
  }
  return { wantPlay, ended }
}
