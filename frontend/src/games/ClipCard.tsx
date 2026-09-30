import { useEffect, useRef, useState } from 'react'
import { SOURCE_DESCRIPTION, eventTime, sourceKindOf, sourceLabel } from '../components/util'
import { formatDuration } from './format'
import type { ClipRef, GamesLeague } from './types'

/** Poster thumbnail that expands into an inline video player. */
export function ClipCard({ clip, alternates, league, isNew, reason, compact }: {
  clip: ClipRef; alternates?: ClipRef[] | null; league: GamesLeague; isNew?: boolean; reason?: string | null; compact?: boolean
}) {
  const options = [clip, ...(alternates ?? []).filter(c => c && c.event_id !== clip.event_id)]
  const [activeId, setActiveId] = useState(clip.event_id)
  const [playing, setPlaying] = useState(false)
  const [failed, setFailed] = useState(false)
  const active = options.find(c => c.event_id === activeId) ?? clip
  const kind = sourceKindOf({ source_kind: active.source_kind })
  const duration = formatDuration(active.duration_seconds)
  const videoRef = useRef<HTMLVideoElement>(null)
  const kindsDistinct = new Set(options.map(c => sourceKindOf({ source_kind: c.source_kind }))).size === options.length

  useEffect(() => {
    if (!playing) return
    setFailed(false)
    videoRef.current?.play().catch(() => { /* autoplay blocked: controls remain */ })
  }, [playing, active.event_id])

  return (
    <div className={`clip-card ${compact ? 'compact' : ''} ${isNew ? 'new-clip' : ''}`} data-clip-id={active.event_id}>
      <div className="clip-media">
        {playing && active.video_url && !failed ? (
          <video ref={videoRef} key={active.event_id} src={active.video_url} poster={active.poster_url ?? undefined}
            controls autoPlay playsInline onError={() => setFailed(true)} />
        ) : (
          <button className="clip-poster" aria-label={`Play clip: ${active.title}`} disabled={!active.video_url}
            style={{ backgroundImage: active.poster_url ? `url("${active.poster_url}")` : undefined }}
            onClick={() => setPlaying(true)}>
            {!active.poster_url && <span className="clip-poster-fallback">{active.title}</span>}
            {active.video_url && <span className="play-btn" aria-hidden>▶</span>}
            {duration && <span className="clip-duration mono">{duration}</span>}
            {(failed || !active.video_url) && <span className="clip-failed">Video unavailable</span>}
          </button>
        )}
        {isNew && <span className="new-clip-flash">NEW CLIP</span>}
      </div>
      <div className="clip-meta">
        <span className={`source-pill ${kind}`} data-source-kind={kind} title={SOURCE_DESCRIPTION[kind]}>{sourceLabel(kind, league)}</span>
        {active.social_score != null && <span className="social-score" title="Social score">🔥 {active.social_score.toFixed(2)}</span>}
        {compact && <span className="clip-title">{active.title}</span>}
        {!compact && reason && <span className="viral-reason muted">{reason}</span>}
        {compact && active.occurred_utc && <time className="muted" dateTime={active.occurred_utc}>{eventTime(active.occurred_utc)}</time>}
      </div>
      {options.length > 1 && (
        <div className="alt-clips" role="group" aria-label="Alternate clips">
          <span className="muted">Also:</span>
          {options.map(c => {
            // label by provenance when that tells them apart, else by title (e.g. "Field View", Spanish feed)
            const label = kindsDistinct ? sourceLabel(sourceKindOf({ source_kind: c.source_kind }), league) : c.title
            return <button key={c.event_id} className={c.event_id === active.event_id ? 'on' : ''} data-clip-id={c.event_id}
              title={c.title} onClick={() => setActiveId(c.event_id)}>{label}</button>
          })}
        </div>
      )}
    </div>
  )
}
