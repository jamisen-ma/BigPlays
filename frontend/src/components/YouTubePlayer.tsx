import { useEffect, useRef, useState } from 'react'

/**
 * Broadcast footage player. Uses the YouTube IFrame Player API under the hood but
 * presents the footage as the agent's own auto-cut clip: native controls hidden,
 * title bar / watermark cropped out, pointer events blocked so no YouTube chrome
 * ever appears, our own transport bar drawn on top, and the clip loops inside its
 * cut window without ever reaching the end screen. Reports errors (e.g. 150 =
 * owner disabled third-party embedding) so the caller can fall back.
 */

declare global {
  interface Window {
    YT?: any
    onYouTubeIframeAPIReady?: () => void
  }
}

let apiPromise: Promise<any> | null = null
function loadApi(): Promise<any> {
  if (window.YT?.Player) return Promise.resolve(window.YT)
  if (!apiPromise) {
    apiPromise = new Promise(resolve => {
      const prev = window.onYouTubeIframeAPIReady
      window.onYouTubeIframeAPIReady = () => { prev?.(); resolve(window.YT) }
      const s = document.createElement('script')
      s.src = 'https://www.youtube.com/iframe_api'
      document.head.appendChild(s)
    })
  }
  return apiPromise
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

export function YouTubePlayer({ videoId, start = 0, end, onError }: {
  videoId: string; start?: number; end?: number | null; onError?: (code: number) => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const player = useRef<any>(null)
  const onErrorRef = useRef(onError); onErrorRef.current = onError
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(true)
  const [t, setT] = useState(0)
  const [dur, setDur] = useState(0)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let cancelled = false
    let poll: number | undefined
    const timeout = window.setTimeout(() => onErrorRef.current?.(-1), 15000)
    const el = document.createElement('div')
    host.current?.appendChild(el)
    setReady(false); setPlaying(false); setT(0); setDur(0)
    loadApi().then(YT => {
      if (cancelled) return
      player.current = new YT.Player(el, {
        videoId,
        host: 'https://www.youtube-nocookie.com',
        playerVars: {
          autoplay: 1, mute: 1, controls: 0, rel: 0, modestbranding: 1, playsinline: 1,
          iv_load_policy: 3, disablekb: 1, fs: 0, start, origin: window.location.origin,
        },
        events: {
          onReady: (e: any) => {
            e.target.mute(); e.target.playVideo()
            setReady(true)
            const total = end ?? Math.max(0, e.target.getDuration() - 0.8)
            setDur(Math.max(0, total - start))
            poll = window.setInterval(() => {
              const p = player.current
              if (!p?.getCurrentTime) return
              const cur = p.getCurrentTime()
              const stop = end ?? Math.max(0, p.getDuration() - 0.8)
              // loop inside the cut window so the end screen never shows
              if (stop > 0 && cur >= stop) { p.seekTo(start, true); p.playVideo(); return }
              setT(Math.max(0, cur - start))
            }, 200)
          },
          onError: (e: any) => onErrorRef.current?.(Number(e.data)),
          onStateChange: (e: any) => {
            if (e.data === YT.PlayerState.PLAYING) window.clearTimeout(timeout)
            setPlaying(e.data === YT.PlayerState.PLAYING || e.data === YT.PlayerState.BUFFERING)
            if (e.data === YT.PlayerState.ENDED) { e.target.seekTo(start, true); e.target.playVideo() }
          },
        },
      })
    })
    return () => {
      cancelled = true
      window.clearTimeout(timeout)
      if (poll) window.clearInterval(poll)
      try { player.current?.destroy() } catch { /* ignore */ }
      player.current = null
      el.remove()
    }
  }, [videoId, start, end])

  const toggle = () => {
    const p = player.current
    if (!p) return
    playing ? p.pauseVideo() : p.playVideo()
  }
  const toggleMute = () => {
    const p = player.current
    if (!p) return
    if (muted) { p.unMute(); p.setVolume(80) } else { p.mute() }
    setMuted(!muted)
  }
  const replay = () => { const p = player.current; if (!p) return; p.seekTo(start, true); p.playVideo() }
  const seek = (frac: number) => { const p = player.current; if (!p || !dur) return; p.seekTo(start + frac * dur, true) }

  return (
    <div className="clip-player">
      <div className="clip-crop"><div className="yt-host" ref={host} /></div>
      <div className="clip-mask-top" />
      <div className="clip-shield" onClick={toggle} />
      {!ready && <div className="clip-loading"><span className="spinner" /> loading highlight…</div>}
      <div className="clip-bar">
        <button onClick={toggle} title={playing ? 'Pause' : 'Play'}>{playing ? '❚❚' : '▶'}</button>
        <button onClick={replay} title="Replay">↺</button>
        <div className="clip-track" onClick={e => { const r = (e.currentTarget as HTMLDivElement).getBoundingClientRect(); seek((e.clientX - r.left) / r.width) }}>
          <div className="clip-fill" style={{ width: dur ? `${Math.min(100, (t / dur) * 100)}%` : '0%' }} />
        </div>
        <span className="clip-time mono">{fmt(t)} / {fmt(dur)}</span>
        <button onClick={toggleMute} title={muted ? 'Unmute' : 'Mute'}>{muted ? '🔇' : '🔊'}</button>
      </div>
    </div>
  )
}
