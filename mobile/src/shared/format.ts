// Ported from frontend/src/games/format.ts (+ Situation.tsx helpers). Pure TS.
import { isNflSituation, type GamesLeague, type NflSituation, type Play, type ScoreGame, type Team } from './types'

const LA = 'America/Los_Angeles'

/** Today's date (YYYYMMDD) in America/Los_Angeles, matching the API default. */
export function todayKey(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: LA, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now)
  const get = (t: string) => parts.find(p => p.type === t)?.value ?? ''
  return `${get('year')}${get('month')}${get('day')}`
}

export const isDateKey = (s: string | null | undefined): s is string => !!s && /^\d{8}$/.test(s)

function keyToUtc(key: string): Date {
  return new Date(Date.UTC(+key.slice(0, 4), +key.slice(4, 6) - 1, +key.slice(6, 8)))
}

export function shiftDate(key: string, days: number): string {
  const d = keyToUtc(key)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10).replace(/-/g, '')
}

export function dateLabel(key: string, today = todayKey()): string {
  const rel = key === today ? 'Today' : key === shiftDate(today, -1) ? 'Yesterday' : key === shiftDate(today, 1) ? 'Tomorrow' : ''
  const text = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(keyToUtc(key))
  return rel ? `${rel} · ${text}` : text
}

export function localStartTime(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

/** ESPN colors come with or without '#'. Falls back to the theme's line color. */
export function teamColor(team: Pick<Team, 'color'> | null | undefined, fallback = '#2a3242'): string {
  const c = team?.color?.trim()
  if (!c) return fallback
  return c.startsWith('#') ? c : `#${c}`
}

/** "Bottom 7" / "Bot 7" / "Top 7th" -> "Bot 7" / "Top 7". NFL labels pass through. */
export function periodName(label: string | null, league: GamesLeague, period?: number | null): string {
  if (!label) return period != null ? (league === 'nfl' ? (period > 4 ? 'OT' : `Q${period}`) : `Inning ${period}`) : '—'
  const m = /^(top|bot(?:tom)?|mid(?:dle)?|end)\s*(\d+)/i.exec(label)
  if (!m) return label
  const h = m[1].toLowerCase()
  const half = h.startsWith('top') ? 'Top' : h.startsWith('bot') ? 'Bot' : h.startsWith('mid') ? 'Mid' : 'End'
  return `${half} ${m[2]}`
}

/** Status line for a game: live period/clock, "Final", or the local start time. */
export function statusText(g: ScoreGame): string {
  if (g.status === 'pre') return localStartTime(g.start_utc) ?? g.status_detail ?? 'Scheduled'
  if (g.status === 'post') return g.status_detail || 'Final'
  if (g.league === 'nfl') {
    if (g.status_detail && /half|end of|delay/i.test(g.status_detail)) return g.status_detail
    return [periodName(g.period_label, 'nfl', g.period), g.clock].filter(Boolean).join(' · ')
  }
  return g.period_label ? periodName(g.period_label, 'mlb', g.period) : g.status_detail ?? 'Live'
}

const STATUS_RANK: Record<string, number> = { in: 0, pre: 1, post: 2 }

/** Live games first, then upcoming, then final; each by start time. */
export function sortGames(games: ScoreGame[]): ScoreGame[] {
  return [...games].sort((a, b) => (STATUS_RANK[a.status] ?? 3) - (STATUS_RANK[b.status] ?? 3)
    || (Date.parse(a.start_utc ?? '') || 0) - (Date.parse(b.start_utc ?? '') || 0)
    || a.game_id.localeCompare(b.game_id))
}

export interface PlayGroup { key: string; label: string; plays: Play[] }

/** Group consecutive plays (any input order; sorted by sequence) by period label. */
export function groupPlays(plays: Play[], league: GamesLeague, latestFirst: boolean): PlayGroup[] {
  const groups: PlayGroup[] = []
  for (const p of [...plays].sort((a, b) => a.sequence - b.sequence)) {
    const label = periodName(p.period_label, league, p.period)
    const last = groups[groups.length - 1]
    if (last && last.label === label) last.plays.push(p)
    else groups.push({ key: `${groups.length}-${label}`, label, plays: [p] })
  }
  if (!latestFirst) return groups
  return groups.reverse().map(g => ({ ...g, plays: [...g.plays].reverse() }))
}

export function playWhen(p: Play, league: GamesLeague): string {
  if (league === 'mlb') {
    const m = p.mlb
    if (!m) return ''
    const count = m.balls != null && m.strikes != null ? `${m.balls}-${m.strikes}` : ''
    const outs = m.outs != null ? `${m.outs} out` : ''
    return [count, outs].filter(Boolean).join(', ')
  }
  return p.clock ?? ''
}

export function formatDuration(s: number | null | undefined): string | null {
  if (s == null || !Number.isFinite(s)) return null
  const r = Math.round(s)
  return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, '0')}`
}

export function ordinal(n: number): string {
  return n === 1 ? '1st' : n === 2 ? '2nd' : n === 3 ? '3rd' : `${n}th`
}

/** "3rd & 4 at GB 35", from the backend text or rebuilt from the parts. */
export function downDistanceText(s: NflSituation): string | null {
  return s.down_distance_text
    ?? (s.down ? `${ordinal(s.down)} & ${s.distance ?? '?'}${s.yard_line_text ? ` at ${s.yard_line_text}` : ''}` : null)
}

export const possessionOf = (g: ScoreGame): string | null =>
  g.status === 'in' && g.league === 'nfl' && isNflSituation(g.situation) ? g.situation.possession : null

/** Viral = a clip is attached (contract: "A play is viral when a saved clip is attached"). */
export function viralPlays(plays: Play[]): Play[] {
  return plays.filter(p => p.clip != null)
}
