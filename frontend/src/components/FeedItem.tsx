import type { Highlight } from '../types'
import { HypeMeter } from './HypeMeter'
import { SOURCE_DESCRIPTION, eventTime, gamePhase, hypeOf, posterUrl, sourceKindOf, sourceLabel, timeAgo } from './util'

export function FeedItem({ h, active, fresh, onClick, now }: { h: Highlight; active: boolean; fresh: boolean; onClick: () => void; now: number }) {
  const poster = posterUrl(h)
  const kind = sourceKindOf(h)
  const arrival = kind === 'live_capture' ? `Captured ${timeAgo(h.received_utc, now) || 'time unknown'}`
    : kind === 'official_upload' ? `Imported ${timeAgo(h.received_utc, now) || 'time unknown'}`
    : `Replay arrived ${timeAgo(h.received_utc, now) || 'time unknown'}`
  return (
    <button data-event-id={h.event_id} data-source-kind={kind} className={`feed-item ${active ? 'active' : ''} ${fresh ? 'fresh' : ''}`} onClick={onClick}>
      <div className="feed-thumb" style={{ backgroundImage: poster ? `url(${poster})` : undefined }}>
        {!poster && <span className="feed-thumb-fallback">{h.league.toUpperCase()}</span>}
        <span className={`league-pill ${h.league}`}>{h.league.toUpperCase()}</span>
        {fresh && <span className="new-pill">NEW</span>}
      </div>
      <div className="feed-body">
        <div className="feed-title">{h.title}</div>
        <div className="feed-meta">
          <span className={`source-pill ${kind}`} title={SOURCE_DESCRIPTION[kind]}>{sourceLabel(kind, h.league)}</span>
          <span className="mono">{h.away} {h.away_score} · {h.home} {h.home_score}</span>
          <span className="dot">•</span>
          <span>{gamePhase(h)}</span>
        </div>
        <div className="feed-event-time" title={h.occurred_utc ?? undefined}>
          <time dateTime={h.occurred_utc ?? undefined}>{eventTime(h.occurred_utc)}</time>
          <span className="muted">{arrival}</span>
        </div>
        <div className="feed-tags">
          {h.social_assessment?.fan_backed && <span className="tag">Fan-backed highlight</span>}
          {(h.tags ?? []).slice(0, 3).map(t => <span key={t} className="tag">#{t.replace(/\s+/g, '')}</span>)}
        </div>
      </div>
      {h.imported ? <span className="saved-duration mono" aria-label="Clip duration">
        {h.clip_duration != null ? `${Math.round(h.clip_duration)}s` : 'Saved'}
      </span> : <HypeMeter value={hypeOf(h)} size={48} stroke={5} label="" />}
    </button>
  )
}
