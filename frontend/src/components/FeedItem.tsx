import type { Highlight } from '../types'
import { HypeMeter } from './HypeMeter'
import { hypeOf, posterUrl, timeAgo } from './util'

export function FeedItem({ h, active, fresh, onClick, now }: { h: Highlight; active: boolean; fresh: boolean; onClick: () => void; now: number }) {
  const poster = posterUrl(h)
  return (
    <button className={`feed-item ${active ? 'active' : ''} ${fresh ? 'fresh' : ''}`} onClick={onClick}>
      <div className="feed-thumb" style={{ backgroundImage: poster ? `url(${poster})` : undefined }}>
        {!poster && <span className="feed-thumb-fallback">{h.league.toUpperCase()}</span>}
        <span className={`league-pill ${h.league}`}>{h.league.toUpperCase()}</span>
        {fresh && <span className="new-pill">NEW</span>}
      </div>
      <div className="feed-body">
        <div className="feed-title">{h.title}</div>
        <div className="feed-meta">
          <span className="mono">{h.away} {h.away_score} · {h.home} {h.home_score}</span>
          <span className="dot">•</span>
          <span>{h.period} {h.clock}</span>
          <span className="dot">•</span>
          <span className="muted">{timeAgo(h.occurred_utc, now)}</span>
        </div>
        <div className="feed-tags">
          {(h.tags ?? []).slice(0, 3).map(t => <span key={t} className="tag">#{t.replace(/\s+/g, '')}</span>)}
        </div>
      </div>
      <HypeMeter value={hypeOf(h)} size={48} stroke={5} label="" />
    </button>
  )
}
