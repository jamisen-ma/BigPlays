import { statusText, teamColor } from './format'
import { SituationView, possessionOf } from './Situation'
import { TeamLogo } from './TeamLogo'
import type { ScoreGame, Team } from './types'

export function StatusPill({ game }: { game: ScoreGame }) {
  const text = statusText(game)
  if (game.status === 'in') return <span className="status live" data-status="in"><i />{text}</span>
  return <span className={`status ${game.status}`} data-status={game.status}>{text}</span>
}

function TeamRow({ team, game, possession }: { team: Team; game: ScoreGame; possession: boolean }) {
  const loser = game.status === 'post' && team.winner === false
  return (
    <div className={`gc-team ${loser ? 'loser' : ''} ${team.winner ? 'winner' : ''}`} style={{ ['--team' as string]: teamColor(team) }}>
      <TeamLogo team={team} size={28} />
      <span className="gc-abbr">{team.abbr}</span>
      {possession && <span className="possession" aria-label={`${team.abbr} ball`} title="Possession">●</span>}
      <span className="gc-record muted">{team.record ?? ''}</span>
      <span className="gc-score mono">{game.status === 'pre' ? '' : team.score ?? '–'}</span>
    </div>
  )
}

export function GameCard({ game, onOpen, href }: { game: ScoreGame; onOpen: () => void; href: string }) {
  const pos = possessionOf(game)
  return (
    <a className={`game-card ${game.status}`} data-game-id={game.game_id} data-status={game.status} href={href}
      onClick={e => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); onOpen() }}
      aria-label={`${game.away.abbr} at ${game.home.abbr}, ${statusText(game)}`}>
      <div className="gc-head">
        <StatusPill game={game} />
        <span className="gc-broadcast muted">{game.broadcast ?? ''}</span>
        {game.clip_count > 0 && <span className="clip-badge" data-clip-count={game.clip_count}>🎬 {game.clip_count} {game.clip_count === 1 ? 'clip' : 'clips'}</span>}
      </div>
      <div className="gc-body">
        <div className="gc-teams">
          <TeamRow team={game.away} game={game} possession={!!pos && pos === game.away.abbr} />
          <TeamRow team={game.home} game={game} possession={!!pos && pos === game.home.abbr} />
        </div>
        {game.status === 'in' && game.situation && <div className="gc-situation"><SituationView game={game} compact /></div>}
      </div>
      {game.status === 'in' && game.last_play_text && <div className="gc-last muted">{game.last_play_text}</div>}
      {game.status === 'pre' && game.venue && <div className="gc-last muted">{game.venue}</div>}
    </a>
  )
}
