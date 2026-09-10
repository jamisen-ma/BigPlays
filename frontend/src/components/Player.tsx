import { useEffect, useRef, useState } from 'react'
import type { Highlight } from '../types'
import { HypeBar, HypeMeter } from './HypeMeter'
import { REASON_LABEL, hypeOf } from './util'
import { YouTubePlayer } from './YouTubePlayer'

export function Player({ h }: { h: Highlight | null }) {
  const ref = useRef<HTMLVideoElement>(null)
  const [muted, setMuted] = useState(true)
  // footage that failed to load falls back to the locally rendered clip
  const [useLocal, setUseLocal] = useState(false)
  useEffect(() => { setUseLocal(false) }, [h?.event_id])

  useEffect(() => {
    const v = ref.current
    if (!v) return
    v.load()
    v.play().catch(() => {})
  }, [h?.file, useLocal])

  if (!h) {
    return (
      <section className="player empty">
        <div className="player-empty">
          <div className="spinner big" />
          <h2>Waiting for the first viral play…</h2>
          <p>The agent is monitoring live scoreboards, commentary and social signals. Clips appear here the moment one crosses the hype threshold.</p>
        </div>
      </section>
    )
  }

  const src = h.file ? `/clips/${h.file}` : undefined
  const poster = h.poster ? `/clips/${h.poster}` : undefined
  const teamColor = h.team === h.home ? h.home_color : h.away_color
  const showFootage = !!h.youtube_id && !useLocal

  return (
    <section className="player">
      <div className="video-wrap" style={{ ['--team' as string]: teamColor ?? 'var(--accent)' }}>
        {showFootage ? (
          <YouTubePlayer
            key={`ft-${h.event_id}`}
            videoId={h.youtube_id!}
            start={h.youtube_start ?? 0}
            end={h.youtube_end ?? null}
            onError={() => setUseLocal(true)}
          />
        ) : src ? (
          <video ref={ref} key={h.event_id} src={src} poster={poster} autoPlay loop muted={muted} playsInline controls />
        ) : (
          <div className="video-missing">clip file missing</div>
        )}
        <div className="video-badges">
          <span className={`league-pill ${h.league}`}>{h.league.toUpperCase()}</span>
          <span className="live-pill"><i /> AUTO-CLIPPED</span>
          <span className="cut-pill mono">{h.youtube_end != null ? `${h.youtube_end - (h.youtube_start ?? 0)}s cut` : '−8s / +6s'}</span>
        </div>
        {!showFootage && (
          <div className="video-tools">
            <button className="mute" onClick={() => setMuted(m => !m)} title={muted ? 'Unmute' : 'Mute'}>{muted ? '🔇' : '🔊'}</button>
          </div>
        )}
      </div>

      <div className="detail">
        <div className="detail-head">
          <div>
            <div className="detail-game">
              <span className="team" style={{ ['--c' as string]: h.away_color }}>{h.away} <b>{h.away_score}</b></span>
              <span className="at">@</span>
              <span className="team" style={{ ['--c' as string]: h.home_color }}>{h.home} <b>{h.home_score}</b></span>
              <span className="clock mono">{h.period} {h.clock}</span>
            </div>
            <h1 className="headline">{h.title}</h1>
            <p className="pbp">{h.description}</p>
          </div>
          <HypeMeter value={hypeOf(h)} size={92} stroke={8} />
        </div>

        <div className="chips">
          {(h.reasons ?? []).map(r => <span key={r} className={`chip reason ${r}`}>{REASON_LABEL[r] ?? r}</span>)}
          {(h.tags ?? []).map(t => <span key={t} className="chip tag">#{t.replace(/\s+/g, '')}</span>)}
        </div>

        <div className="detail-grid">
          <div className="card">
            <div className="card-title"><span className="claude-dot" /> Claude judgment</div>
            <p className="rationale">{h.llm?.rationale ?? 'Heuristics only (LLM disabled).'}</p>
            <div className="scores">
              <HypeBar value={h.base_score ?? 0} label="heuristic base" />
              <HypeBar value={h.social_score ?? 0} label="social burst" />
              <HypeBar value={h.llm?.hype_score ?? 0} label="LLM hype" />
              <HypeBar value={h.combined_score ?? 0} label="combined" />
            </div>
          </div>
          <div className="card">
            <div className="card-title">Retrieved context (RAG)</div>
            <ul className="quotes">
              {(h.commentary ?? []).map((c, i) => <li key={i}><span className="q-src">PBP</span>{c}</li>)}
              {(h.social ?? []).map((c, i) => <li key={`s${i}`}><span className="q-src social">SOC</span>{c}</li>)}
              {!(h.commentary?.length || h.social?.length) && <li className="muted">no retrieved context for this event</li>}
            </ul>
          </div>
        </div>

        <div className="storage mono">
          <span className="muted">stored →</span> {h.storage_uri ?? (h.file ? `file://data/clips/${h.file}` : '—')}
          <span className="muted"> · event {h.event_id}</span>
        </div>
      </div>
    </section>
  )
}
