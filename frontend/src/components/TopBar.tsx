import type { ReactNode } from 'react'
import type { Game, Highlight, League } from '../types'
import { hypeOf } from './util'

export function TopBar({ nav, connected, highlights, games, filter, setFilter, follow, setFollow }: {
  connected: boolean; highlights: Highlight[]; games: Game[];
  filter: 'all' | League; setFilter: (f: 'all' | League) => void;
  follow: boolean; setFollow: (b: boolean) => void; nav?: ReactNode;
}) {
  const avg = highlights.length ? highlights.reduce((a, h) => a + hypeOf(h), 0) / highlights.length : 0
  const live = games.filter(g => g.status === 'in').length
  const archived = highlights.some(h => h.imported)
  const includesMLB = games.some(g => g.league === 'mlb') || highlights.some(h => h.league === 'mlb')
  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo">BIG<span>PLAYS</span></span>
        {nav}
        <span className={`conn ${connected ? 'on' : 'off'}`}><i />{connected ? filter === 'mlb' || (filter === 'all' && includesMLB)
          ? 'CONNECTED' : 'LIVE' : 'RECONNECTING'}</span>
        {filter === 'mlb' && <span className="mode">MLB · TODAY</span>}
      </div>
      <div className="stats">
        <Stat label={archived ? 'games in library' : 'games monitored'} value={archived ? new Set(highlights.map(h => h.game_id)).size : live} />
        <Stat label={archived ? 'saved highlights' : 'viral plays'} value={highlights.length} />
        {!archived && <><Stat label="avg hype" value={avg ? avg.toFixed(2) : '—'} />
          <Stat label="model" value="claude" mono /></>}
      </div>
      <div className="controls">
        <div className="seg">
          {(['all', 'nba', 'nfl', 'mlb', 'ncaaf'] as const).map(f => (
            <button key={f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>{f === 'ncaaf' ? 'CFB' : f.toUpperCase()}</button>
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
