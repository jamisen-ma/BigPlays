import type { MlbSituation, NflSituation, ScoreGame } from './types'
import { isMlbSituation, isNflSituation } from './types'

export function Diamond({ s, size = 34 }: { s: Pick<MlbSituation, 'on_first' | 'on_second' | 'on_third'>; size?: number }) {
  const base = (on: boolean, x: number, y: number, name: string) => (
    <rect x={x - 6} y={y - 6} width={12} height={12} transform={`rotate(45 ${x} ${y})`}
      className={on ? 'base on' : 'base'} data-base={name} data-on={on ? 'true' : 'false'} />
  )
  const runners = [s.on_first && '1st', s.on_second && '2nd', s.on_third && '3rd'].filter(Boolean)
  return (
    <svg className="diamond" width={size} height={size * 0.8} viewBox="0 0 40 32" role="img"
      aria-label={runners.length ? `Runners on ${runners.join(', ')}` : 'Bases empty'}>
      {base(s.on_second, 20, 9, 'second')}
      {base(s.on_third, 9, 20, 'third')}
      {base(s.on_first, 31, 20, 'first')}
    </svg>
  )
}

export function Outs({ outs }: { outs: number | null }) {
  return (
    <span className="outs" aria-label={outs != null ? `${outs} ${outs === 1 ? 'out' : 'outs'}` : 'Outs unknown'}>
      {[0, 1, 2].map(i => <i key={i} className={outs != null && i < outs ? 'on' : ''} />)}
    </span>
  )
}

export function MlbSituationView({ s, compact }: { s: MlbSituation; compact?: boolean }) {
  const count = s.balls != null && s.strikes != null ? `${s.balls}-${s.strikes}` : '–'
  return (
    <div className={`mlb-situation ${compact ? 'compact' : ''}`}>
      <Diamond s={s} size={compact ? 34 : 48} />
      <div className="bso">
        <span className="count mono" aria-label="Balls-strikes">{count}</span>
        <Outs outs={s.outs} />
        <span className="outs-text">{s.outs != null ? `${s.outs} out` : ''}</span>
      </div>
      {!compact && (s.batter || s.pitcher) && (
        <div className="matchup">
          {s.batter && <div><span className="muted">AB</span> {s.batter}</div>}
          {s.pitcher && <div><span className="muted">P</span> {s.pitcher}</div>}
        </div>
      )}
    </div>
  )
}

export function NflSituationView({ s }: { s: NflSituation }) {
  const text = s.down_distance_text
    ?? (s.down ? `${ordinal(s.down)} & ${s.distance ?? '?'}${s.yard_line_text ? ` at ${s.yard_line_text}` : ''}` : null)
  if (!text) return null
  return (
    <div className={`nfl-situation ${s.is_red_zone ? 'red-zone' : ''}`}>
      <span className="down-distance">{text}</span>
      {s.is_red_zone && <span className="rz-pill">RED ZONE</span>}
    </div>
  )
}

export function SituationView({ game, compact }: { game: ScoreGame; compact?: boolean }) {
  if (game.status !== 'in' || !game.situation) return null
  if (game.league === 'mlb' && isMlbSituation(game.situation)) return <MlbSituationView s={game.situation} compact={compact} />
  if (game.league === 'nfl' && isNflSituation(game.situation)) return <NflSituationView s={game.situation} />
  return null
}

export const possessionOf = (g: ScoreGame): string | null =>
  g.status === 'in' && g.league === 'nfl' && isNflSituation(g.situation) ? g.situation.possession : null

function ordinal(n: number) { return n === 1 ? '1st' : n === 2 ? '2nd' : n === 3 ? '3rd' : `${n}th` }
