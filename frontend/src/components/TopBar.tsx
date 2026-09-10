import type { Game, Highlight } from '../types'
import { hypeOf } from './util'

export function TopBar({ connected, mode, highlights, games, filter, setFilter, follow, setFollow }: {
  connected: boolean; mode: 'demo' | 'live' | null; highlights: Highlight[]; games: Game[];
  filter: 'all' | 'nba' | 'nfl'; setFilter: (f: 'all' | 'nba' | 'nfl') => void;
  follow: boolean; setFollow: (b: boolean) => void;
}) {
  const avg = highlights.length ? highlights.reduce((a, h) => a + hypeOf(h), 0) / highlights.length : 0
  const live = games.filter(g => g.status === 'in').length
  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo">BIG<span>PLAYS</span></span>
        <span className={`conn ${connected ? 'on' : 'off'}`}><i />{connected ? 'LIVE' : 'RECONNECTING'}</span>
        {mode === 'demo' && <span className="mode">DEMO REPLAY · no live games tonight</span>}
      </div>
      <div className="stats">
        <Stat label="games monitored" value={live} />
        <Stat label="viral plays" value={highlights.length} />
        <Stat label="avg hype" value={avg ? avg.toFixed(2) : '—'} />
        <Stat label="model" value="claude" mono />
      </div>
      <div className="controls">
        <div className="seg">
          {(['all', 'nba', 'nfl'] as const).map(f => (
            <button key={f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>{f.toUpperCase()}</button>
          ))}
        </div>
        <label className="toggle">
          <input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} />
          <span>auto-play new</span>
        </label>
      </div>
    </header>
  )
}

function Stat({ label, value, mono }: { label: string; value: string | number; mono?: boolean }) {
  return (
    <div className="stat">
      <div className={`stat-v ${mono ? 'mono' : ''}`}>{value}</div>
      <div className="stat-l">{label}</div>
    </div>
  )
}
