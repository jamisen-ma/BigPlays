// Linescore -> display columns. Logic ported from frontend/src/games/Linescore.tsx. Pure TS.
import type { Linescore, MlbTotals, NflTotals, ScoreGame } from './types'

const isMlbTotals = (t: MlbTotals | NflTotals | undefined | null): t is MlbTotals => !!t && 'R' in t

export type Cell = number | string
export interface LinescoreColumns {
  periods: { label: string; away: Cell; home: Cell }[]
  totals: { label: string; away: Cell; home: Cell }[]
}

const cell = (v: number | null | undefined): Cell => (v == null ? '–' : v)

/** Pads MLB to 9 innings and NFL to 4 quarters so it reads like a box score. Null when there's nothing to show. */
export function linescoreColumns(game: Pick<ScoreGame, 'league' | 'away' | 'home'>, ls: Linescore | null | undefined): LinescoreColumns | null {
  if (!ls?.periods?.length) return null
  const mlb = game.league === 'mlb' || isMlbTotals(ls.totals?.away)
  const periods = ls.periods.map(p => ({ label: p.label, away: cell(p.away), home: cell(p.home) }))
  for (let i = periods.length; i < (mlb ? 9 : 4); i++) periods.push({ label: String(i + 1), away: '–', home: '–' })
  const total = (side: 'away' | 'home', col: 'R' | 'H' | 'E' | 'T'): Cell => {
    const t = ls.totals?.[side]
    if (isMlbTotals(t)) return col === 'T' ? cell(t.R) : cell(t[col])
    return cell((t as NflTotals | undefined)?.score ?? game[side].score)
  }
  const cols = mlb ? (['R', 'H', 'E'] as const) : (['T'] as const)
  return { periods, totals: cols.map(c => ({ label: c, away: total('away', c), home: total('home', c) })) }
}
