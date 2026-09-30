import type { Linescore as LinescoreT, MlbTotals, NflTotals, ScoreGame } from './types'

const isMlbTotals = (t: MlbTotals | NflTotals | undefined): t is MlbTotals => !!t && 'R' in t

export function Linescore({ game, linescore }: { game: ScoreGame; linescore: LinescoreT | null }) {
  if (!linescore?.periods?.length) return null
  const mlb = game.league === 'mlb' || isMlbTotals(linescore.totals?.away)
  // pad MLB to 9 innings and NFL to 4 quarters so the table reads like a box score
  const minPeriods = mlb ? 9 : 4
  const periods = [...linescore.periods]
  for (let i = periods.length; i < minPeriods; i++) periods.push({ label: String(i + 1), away: null, home: null })
  const cell = (v: number | null | undefined) => v == null ? '–' : v
  const totalCols = mlb ? ['R', 'H', 'E'] as const : ['T'] as const
  const total = (side: 'away' | 'home', col: string) => {
    const t = linescore.totals?.[side]
    if (isMlbTotals(t)) return cell(t[col as 'R' | 'H' | 'E'])
    return cell((t as NflTotals | undefined)?.score ?? game[side].score)
  }
  return (
    <div className="linescore-wrap">
      <table className="linescore mono" aria-label="Linescore">
        <thead><tr><th />{periods.map((p, i) => <th key={i}>{p.label}</th>)}{totalCols.map(c => <th key={c} className="tot">{c}</th>)}</tr></thead>
        <tbody>
          {(['away', 'home'] as const).map(side => (
            <tr key={side}>
              <th scope="row">{game[side].abbr}</th>
              {periods.map((p, i) => <td key={i}>{cell(p[side])}</td>)}
              {totalCols.map(c => <td key={c} className="tot">{total(side, c)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
