import type { Highlight } from '../types'

export const hypeOf = (h: Highlight) => h.llm?.hype_score ?? h.combined_score ?? 0

export function hypeColor(v: number): string {
  if (v >= 0.93) return 'var(--hype-max)'
  if (v >= 0.85) return 'var(--hype-hi)'
  if (v >= 0.7) return 'var(--hype-mid)'
  return 'var(--hype-lo)'
}

export function timeAgo(iso?: string, nowMs = Date.now()): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  const s = Math.max(0, Math.round((nowMs - t) / 1000))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return `${h}h ago`
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
