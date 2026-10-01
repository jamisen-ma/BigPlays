// Ported from frontend/src/components/util.ts (sourceKindOf / sourceLabel / timeAgo / eventTime / posterUrl). Pure TS.
import { SOURCE_KINDS, type Highlight, type SourceKind } from './types'

/**
 * Provenance of a clip. Prefer the backend's explicit `source_kind`; otherwise infer it:
 * imported records are official uploads, and anything else was cut by our own pipeline from
 * the continuous stream buffer.
 */
export function sourceKindOf(h: { source_kind?: SourceKind | null; imported?: boolean }): SourceKind {
  if (h.source_kind && SOURCE_KINDS.includes(h.source_kind)) return h.source_kind
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
  replay: 'Replay of archived footage',
  official_upload: 'Official highlight imported after publication',
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
  if (h < 48) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export function eventTime(iso?: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** Poster path for a /api/highlights record (relative; resolve with absUrl). */
export function highlightPoster(h: Pick<Highlight, 'youtube_id' | 'poster'>): string | null {
  if (h.youtube_id) return `https://i.ytimg.com/vi/${h.youtube_id}/mqdefault.jpg`
  return h.poster ? `/clips/${h.poster}` : null
}

/** Video path for a /api/highlights record (relative; resolve with absUrl). */
export function highlightVideo(h: Pick<Highlight, 'file'>): string | null {
  return h.file ? `/clips/${h.file}` : null
}

export const hypeOf = (h: Pick<Highlight, 'llm' | 'combined_score'>): number => h.llm?.hype_score ?? h.combined_score ?? 0

/** Newest first by occurred/published/received time. */
export function sortHighlights<T extends Pick<Highlight, 'occurred_utc' | 'published_utc' | 'received_utc'>>(items: T[]): T[] {
  const t = (h: T) => Date.parse(h.occurred_utc ?? h.published_utc ?? h.received_utc ?? '') || 0
  return [...items].sort((a, b) => t(b) - t(a))
}

/** Primary clip + distinct alternates (ClipCard's "Also:" row). */
export function clipOptions<T extends { event_id: string }>(clip: T, alternates?: (T | null | undefined)[] | null): T[] {
  const seen = new Set([clip.event_id])
  const out = [clip]
  for (const c of alternates ?? []) {
    if (c && !seen.has(c.event_id)) { seen.add(c.event_id); out.push(c) }
  }
  return out
}

/** Label alternates by provenance when that tells them apart, else by title (e.g. "Field View"). */
export function alternateLabels<T extends { title: string; source_kind?: SourceKind | null }>(options: T[], league?: string): string[] {
  const kinds = options.map(c => sourceKindOf({ source_kind: c.source_kind }))
  const distinct = new Set(kinds).size === options.length
  return options.map((c, i) => (distinct ? sourceLabel(kinds[i], league) : c.title))
}

export type LibraryFilter = 'all' | string

/** Leagues present in the library, NFL/MLB first, for the filter chips. */
export function libraryLeagues(items: Pick<Highlight, 'league'>[]): string[] {
  const order = ['nfl', 'mlb']
  const set = new Set(items.map(h => (h.league || 'unknown').toLowerCase()))
  return [...set].sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b)
  })
}

/** Playable clips for a league filter, newest first. */
export function filterLibrary<T extends Highlight>(items: T[], league: LibraryFilter): T[] {
  const playable = items.filter(h => h.file || h.youtube_id)
  const scoped = league === 'all' ? playable : playable.filter(h => (h.league || 'unknown').toLowerCase() === league)
  return sortHighlights(scoped)
}
