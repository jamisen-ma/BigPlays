import type { Game } from '../types'
import { gamePhase } from './util'

export function GamesTicker({ games }: { games: Game[] }) {
  if (!games.length) return <footer className="ticker empty"><span className="muted">Monitoring ESPN scoreboards · no games in progress</span></footer>
  return (
    <footer className="ticker">
      {games.map(g => (
        <div key={g.game_id} className="game">
          <span className={`league-pill ${g.league}`}>{g.league.toUpperCase()}</span>
          <span className="g-team" style={{ ['--c' as string]: g.away_color }}>{g.away}</span>
          <b className="mono">{g.away_score}</b>
          <span className="g-team" style={{ ['--c' as string]: g.home_color }}>{g.home}</span>
          <b className="mono">{g.home_score}</b>
          <span className="g-clock mono">{gamePhase(g)}</span>
        </div>
      ))}
    </footer>
  )
}
