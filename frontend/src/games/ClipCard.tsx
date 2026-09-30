import { useEffect, useId, useRef, useState } from 'react'
import { SOURCE_DESCRIPTION, eventTime, sourceKindOf, sourceLabel } from '../components/util'
import { claim, noteVolume, startVideo, useAutoplay } from './autoplay'
import { formatDuration } from './format'
import type { ClipRef, GamesLeague } from './types'

/** Poster thumbnail that expands into an inline video player. Poster and <video> share one 16:9
 *  .clip-media frame (letterboxed, never cropped; see games.css); badges sit below it.
 *  Instagram-style: the clip autoplays (muted) while it's on screen and pauses when it scrolls away
 *  (see autoplay.ts); clicking the poster plays it with sound. */
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
  const frameRef = useRef<HTMLDivElement>(null)
  const kindsDistinct = new Set(options.map(c => sourceKindOf({ source_kind: c.source_kind }))).size === options.length
  const showVideo = playing && !!active.video_url && !failed
  const autoplayId = useId()
  const { wantPlay, ended } = useAutoplay(autoplayId, frameRef, videoRef, !!active.video_url && !failed, () => setPlaying(true))
  const userStarted = useRef(false)

  // Once the <video> is mounted (poster click, autoplay, or an alternate picked), start it if wanted.
  useEffect(() => {
    if (!playing) return
    setFailed(false)
    if (userStarted.current || wantPlay.current) startVideo(videoRef.current)
  }, [playing, active.event_id, wantPlay])

  const playNow = () => { userStarted.current = true; claim(autoplayId, true); if (playing) startVideo(videoRef.current); else setPlaying(true) }
  const userGesture = () => (navigator as Navigator & { userActivation?: { isActive: boolean } }).userActivation?.isActive === true

  return (
    <div className={`clip-card ${compact ? 'compact' : ''} ${isNew ? 'new-clip' : ''}`} data-clip-id={active.event_id}>
      <div className="clip-media" ref={frameRef}>
        {showVideo ? (
          <video ref={videoRef} key={active.event_id} src={active.video_url ?? undefined} poster={active.poster_url ?? undefined}
            controls playsInline onError={() => setFailed(true)} onEnded={ended}
            onPlay={() => { if (userGesture()) { userStarted.current = true; claim(autoplayId) } }}
            onVolumeChange={e => noteVolume(e.currentTarget)} />
        ) : (
          <button className="clip-poster" aria-label={`Play clip: ${active.title}`} disabled={!active.video_url}
            style={{ backgroundImage: active.poster_url ? `url("${active.poster_url}")` : undefined }}
            onClick={playNow}>
            {!active.poster_url && <span className="clip-poster-fallback">{active.title}</span>}
            {active.video_url && <span className="play-btn" aria-hidden>▶</span>}
            {duration && <span className="clip-duration mono">{duration}</span>}
            {(failed || !active.video_url) && <span className="clip-failed">Video unavailable</span>}
          </button>
        )}
        {isNew && !showVideo && <span className="new-clip-flash">NEW CLIP</span>}
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
