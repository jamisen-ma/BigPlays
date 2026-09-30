import { useEffect, useState } from 'react'
import type { Game, Highlight } from '../types'
import { gamePhase } from './util'

type MLBStatus = { date: string; games: Game[]; enabled: boolean; checked_at?: string; error?: string | null }

export function MLBGames({ highlights, onSelect }: { highlights: Highlight[]; onSelect: (game: string) => void }) {
  const [status, setStatus] = useState<MLBStatus | null>(null)
  useEffect(() => {
    let disposed = false
    async function refresh() {
      try {
        const response = await fetch('/api/mlb/games', { signal: AbortSignal.timeout(15000) })
        if (!response.ok) return
        const data = await response.json()
        if (!disposed && Array.isArray(data.games)) setStatus(data)
      } catch { /* Keep the last schedule while the next refresh retries. */ }
    }
    void refresh()
    const timer = setInterval(refresh, 60000)
    return () => { disposed = true; clearInterval(timer) }
  }, [])
  if (!status?.enabled) return null
  return <section className="mlb-games" aria-label="MLB games today">
    <div className="mlb-games-heading">
      <h3>MLB · {status.date}</h3>
      <span>Official highlights · updates every minute</span>
    </div>
    {status.error && <p role="status">Unable to refresh MLB: {status.error}</p>}
    <div className="mlb-game-list">
      {status.games.map(game => {
        const saved = highlights.filter(h => h.league === 'mlb' && h.game_id === game.game_id).length
        return <button className="mlb-game-card" key={game.game_id} disabled={!saved} onClick={() => onSelect(game.game_id)}
          aria-label={`${game.away} at ${game.home}, ${saved} saved highlights`}>
          {game.series_description && <span className="mlb-series">{game.series_description}</span>}
          <b>{game.away} {game.away_score ?? ''} <span className="muted">@</span> {game.home} {game.home_score ?? ''}</b>
          <span>{game.status === 'pre' && game.starts_at
            ? new Date(game.starts_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
            : gamePhase(game)}</span>
          <span className="muted">{saved ? `${saved} saved highlights` : 'Highlights pending'}</span>
        </button>
      })}
    </div>
    {!status.games.length && !status.error && <p>No MLB games scheduled for this date.</p>}
  </section>
}
