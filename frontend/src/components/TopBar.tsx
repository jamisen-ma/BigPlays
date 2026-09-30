import type { ReactNode } from 'react'
import type { Game, Highlight, League } from '../types'
import { hypeOf } from './util'

export function TopBar({ nav, connected, mode, highlights, games, filter, setFilter, follow, setFollow }: {
  connected: boolean; mode: 'demo' | 'live' | null; highlights: Highlight[]; games: Game[];
  filter: 'all' | League; setFilter: (f: 'all' | League) => void;
  follow: boolean; setFollow: (b: boolean) => void; nav?: ReactNode;
}) {
  const avg = highlights.length ? highlights.reduce((a, h) => a + hypeOf(h), 0) / highlights.length : 0
  const live = games.filter(g => g.status === 'in').length
  const archived = highlights.some(h => h.imported)
  const replayGames = games.filter(g => filter === 'all' || g.league === filter)
  const nflReplay = replayGames.length > 0 && replayGames.every(g => g.league === 'nfl') && replayGames[0].week
  const includesMLB = games.some(g => g.league === 'mlb') || highlights.some(h => h.league === 'mlb')
  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo">BIG<span>PLAYS</span></span>
        {nav}
        <span className={`conn ${connected ? 'on' : 'off'}`}><i />{connected ? filter === 'mlb' || (filter === 'all' && includesMLB)
          ? 'CONNECTED' : mode === 'demo' ? 'REPLAY' : 'LIVE' : 'RECONNECTING'}</span>
        {(mode === 'demo' || filter === 'mlb') && <span className="mode">{filter === 'mlb' ? 'MLB · TODAY'
          : filter === 'all' && includesMLB ? 'NFL REPLAY + MLB HIGHLIGHTS'
          : nflReplay ? `NFL ${replayGames[0].season} · WEEK ${replayGames[0].week} REPLAY` : 'HIGHLIGHT LIBRARY · REPLAY'}</span>}
      </div>
      <div className="stats">
        <Stat label={archived ? 'games in library' : 'games monitored'} value={archived ? new Set(highlights.map(h => h.game_id)).size : live} />
        <Stat label={archived ? 'saved highlights' : 'viral plays'} value={highlights.length} />
        {!archived && <><Stat label="avg hype" value={avg ? avg.toFixed(2) : '—'} />
          <Stat label={mode === 'demo' ? 'analysis' : 'model'} value={mode === 'demo' ? 'simulated' : 'claude'} mono /></>}
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
