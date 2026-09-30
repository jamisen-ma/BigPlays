import { SOURCE_KINDS, type BaseballState, type Highlight, type SourceKind } from '../types'

export function gamePhase(game: BaseballState & { league?: string; period?: string | number; clock?: string; status?: string }): string {
  if (game.league !== 'mlb') return game.clock ? `${game.period ?? ''} ${game.clock}`.trim() : 'Clock unavailable'
  if (game.status === 'final' || game.status === 'post') return 'Final'
  if (game.status === 'pre') return 'Scheduled'
  const half = game.inning_half ? game.inning_half[0].toUpperCase() + game.inning_half.slice(1).toLowerCase() : ''
  const inning = game.inning ? `${half ? half + ' ' : 'Inning '}${game.inning}` : game.period || 'Inning unavailable'
  const count = game.balls != null && game.strikes != null
    ? `${game.balls}–${game.strikes} count${game.count_context ? ` (${game.count_context})` : ''}` : ''
  const outs = game.outs != null ? `${game.outs} ${game.outs === 1 ? 'out' : 'outs'}` : ''
  return [inning, count, outs].filter(Boolean).join(' · ')
}

export const hypeOf = (h: Highlight) => h.llm?.hype_score ?? h.combined_score ?? 0

export function hypeColor(v: number): string {
  if (v >= 0.93) return 'var(--hype-max)'
  if (v >= 0.85) return 'var(--hype-hi)'
  if (v >= 0.7) return 'var(--hype-mid)'
  return 'var(--hype-lo)'
}

export function timeAgo(iso?: string | null, nowMs = Date.now()): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, Math.round((nowMs - t) / 1000))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return `${h}h ago`
}

export function eventTime(iso?: string | null): string {
  if (!iso) return 'Event time unavailable'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Event time unavailable'
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric',
    minute: '2-digit', second: '2-digit', timeZoneName: 'short',
  }).format(date)
}

export function clockTime(ts?: number): string {
  const d = ts ? new Date(ts * 1000) : new Date()
  return d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export const REASON_LABEL: Record<string, string> = {
  lead_change: 'Lead change',
  big_scoring_play: 'Big score',
  clutch_time: 'Clutch',
  social_spike: 'Social spike',
  momentum_swing: 'Momentum',
  llm_viral: 'LLM: viral',
}

export function posterUrl(h: Highlight): string | undefined {
  if (h.youtube_id) return `https://i.ytimg.com/vi/${h.youtube_id}/mqdefault.jpg`
  return h.poster ? `/clips/${h.poster}` : undefined
}

/**
 * Provenance of a clip. Prefer the backend's explicit `source_kind`; otherwise infer it:
 * - demo-dataset records (demo: true, e.g. replay_dataset "nfl-2026-week3") are replays;
 * - other imported records (official MLB/NFL uploads pulled after the fact) are official uploads;
 * - anything else was cut by our own pipeline from the continuous stream buffer.
 */
export function sourceKindOf(h: Pick<Highlight, 'source_kind' | 'demo' | 'imported'>): SourceKind {
  if (h.source_kind && SOURCE_KINDS.includes(h.source_kind)) return h.source_kind
  if (h.demo) return 'replay'
  if (h.imported) return 'official_upload'
  return 'live_capture'
}

export function sourceLabel(kind: SourceKind, league?: string): string {
  if (kind === 'live_capture') return 'LIVE CAPTURE'
  if (kind === 'official_upload') return 'OFFICIAL UPLOAD'
  return league && league !== 'unknown' ? `${league.toUpperCase()} REPLAY` : 'REPLAY'
}

export const SOURCE_DESCRIPTION: Record<SourceKind, string> = {
  live_capture: 'Cut from our own continuous live buffer',
  replay: 'Replay from the demo dataset',
  official_upload: 'Official highlight imported after publication',
}
