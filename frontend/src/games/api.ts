import { useEffect, useRef, useState } from 'react'
import type { GameDetailResponse, GamesLeague, GamesResponse } from './types'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

/** Fetch JSON; non-2xx and `{ok:false, error}` bodies become ApiError (status + server message). */
async function getJson<T extends { ok?: boolean }>(url: string, signal: AbortSignal): Promise<T> {
  const res = await fetch(url, { headers: { accept: 'application/json' }, signal })
  let body: (T & { error?: string; detail?: string }) | null = null
  try { body = await res.json() } catch { /* non-JSON (e.g. an HTML 404) */ }
  if (!res.ok || !body || body.ok === false) {
    const message = body?.error || (typeof body?.detail === 'string' ? body.detail : '')
      || (res.status === 404 ? 'Not found' : res.ok ? 'Invalid response' : `HTTP ${res.status}`)
    throw new ApiError(res.ok ? 0 : res.status, message)
  }
  return body
}

/** Poll intervals from the contract; tests may shrink them via window.__BIGPLAYS_POLL_MS__. */
function pollMs(kind: 'scores' | 'detail'): number {
  const override = (window as unknown as { __BIGPLAYS_POLL_MS__?: Partial<Record<string, number>> }).__BIGPLAYS_POLL_MS__?.[kind]
  return override ?? (kind === 'scores' ? 15_000 : 8_000)
}

// ------------------------------------------------------------------ SSE game_update
type GameUpdate = { league: string; game_id: string }
const listeners = new Set<(u: GameUpdate) => void>()
let source: EventSource | null = null

/** Shared subscription to `game_update {league, game_id}` on the existing /api/stream. */
function subscribeGameUpdates(cb: (u: GameUpdate) => void): () => void {
  listeners.add(cb)
  if (!source && typeof EventSource !== 'undefined') {
    source = new EventSource('/api/stream')
    source.addEventListener('game_update', e => {
      let u: GameUpdate
      try { u = JSON.parse((e as MessageEvent).data) } catch { return }
      if (u && u.game_id) listeners.forEach(l => l(u))
    })
  }
  return () => {
    listeners.delete(cb)
    if (!listeners.size && source) { source.close(); source = null }
  }
}

/**
 * Run `load` now, then every `interval()` ms while it returns true; `poke()` runs it immediately
 * (coalescing with an in-flight run). Returns poke + stop.
 */
function poller(load: () => Promise<boolean>, interval: () => number) {
  let timer: ReturnType<typeof setTimeout> | undefined
  let running = false, again = false, stopped = false
  const run = async () => {
    if (stopped) return
    if (running) { again = true; return }
    running = true
    clearTimeout(timer)
    const keepGoing = await load()
    running = false
    if (stopped) return
    if (again) { again = false; run(); return }
    if (keepGoing) timer = setTimeout(run, interval())
  }
  run()
  return { poke: run, stop: () => { stopped = true; clearTimeout(timer) } }
}

export interface Loadable<T> { data: T | null; error: ApiError | null; loading: boolean; refresh: () => void }

export function useGames(league: GamesLeague, date: string): Loadable<GamesResponse> {
  const [data, setData] = useState<GamesResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    const ctrl = new AbortController()
    let live = false
    setData(prev => (prev && prev.league === league && prev.date === date ? prev : null))
    setLoading(true)
    const p = poller(async () => {
      try {
        const body = await getJson<GamesResponse>(`/api/games?league=${league}&date=${date}`, ctrl.signal)
        const games = Array.isArray(body.games) ? body.games : []
        setData({ ...body, league, date, games })
        setError(null)
        live = games.some(g => g.status === 'in')
      } catch (e) {
        if (ctrl.signal.aborted) return false
        setError(e instanceof ApiError ? e : new ApiError(0, 'Network error'))
      }
      if (!ctrl.signal.aborted) setLoading(false)
      return live // keep polling (and retrying) only while something is live
    }, () => pollMs('scores'))
    const unsub = subscribeGameUpdates(u => { if (u.league === league) p.poke() })
    return () => { ctrl.abort(); p.stop(); unsub() }
  }, [league, date, nonce])

  return { data, error, loading, refresh: () => setNonce(n => n + 1) }
}

export interface GameDetailState extends Loadable<GameDetailResponse> {
  /** play_ids that arrived after the first load (for the "new play" highlight). */
  newPlays: Set<string>
  /** play_ids whose clip was attached after the first load (for the "NEW CLIP" flash). */
  newClips: Set<string>
  /** League the game was actually found in (may differ when the URL had no league). */
  resolvedLeague: GamesLeague
}

const FLASH_MS = 12_000

export function useGameDetail(league: GamesLeague, gameId: string, allowLeagueFallback: boolean): GameDetailState {
  const [data, setData] = useState<GameDetailResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const [resolvedLeague, setResolvedLeague] = useState(league)
  const [newPlays, setNewPlays] = useState<Set<string>>(new Set())
  const [newClips, setNewClips] = useState<Set<string>>(new Set())
  const seen = useRef<Map<string, string | null> | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    const timers: ReturnType<typeof setTimeout>[] = []
    let current = league
    let live = false
    seen.current = null
    setData(null); setLoading(true); setError(null); setNewPlays(new Set()); setNewClips(new Set())
    setResolvedLeague(league)

    const flash = (setter: typeof setNewPlays, ids: string[]) => {
      if (!ids.length) return
      setter(prev => new Set([...prev, ...ids]))
      timers.push(setTimeout(() => setter(prev => {
        const next = new Set(prev); ids.forEach(id => next.delete(id)); return next
      }), FLASH_MS))
    }
    const url = (l: GamesLeague) => `/api/games/${l}/${encodeURIComponent(gameId)}`

    const p = poller(async () => {
      try {
        let body: GameDetailResponse
        try {
          body = await getJson<GameDetailResponse>(url(current), ctrl.signal)
        } catch (e) {
          // deep link without a league: try the other one once
          if (!(e instanceof ApiError && e.status === 404 && allowLeagueFallback && !seen.current && current === league)) throw e
          current = league === 'mlb' ? 'nfl' : 'mlb'
          body = await getJson<GameDetailResponse>(url(current), ctrl.signal)
          setResolvedLeague(current)
        }
        const plays = Array.isArray(body.plays) ? body.plays : []
        const clipsUnmatched = Array.isArray(body.clips_unmatched) ? body.clips_unmatched : []
        const prev = seen.current
        if (prev) {
          flash(setNewPlays, plays.filter(p => !prev.has(p.play_id)).map(p => p.play_id))
          flash(setNewClips, plays.filter(p => p.clip && prev.get(p.play_id) !== p.clip.event_id).map(p => p.play_id))
        }
        seen.current = new Map(plays.map(p => [p.play_id, p.clip?.event_id ?? null]))
        setData({ ...body, plays, clips_unmatched: clipsUnmatched })
        setError(null)
        live = body.game?.status === 'in'
      } catch (e) {
        if (ctrl.signal.aborted) return false
        setError(e instanceof ApiError ? e : new ApiError(0, 'Network error'))
        // keep retrying a game we last saw live
      }
      if (!ctrl.signal.aborted) setLoading(false)
      return live
    }, () => pollMs('detail'))
    // a new clip for this game: refetch right away instead of waiting for the next poll
    const unsub = subscribeGameUpdates(u => { if (u.game_id === gameId && (u.league === current || !u.league)) p.poke() })
    return () => { ctrl.abort(); p.stop(); unsub(); timers.forEach(clearTimeout) }
  }, [league, gameId, allowLeagueFallback, nonce])

  return { data, error, loading, refresh: () => setNonce(n => n + 1), newPlays, newClips, resolvedLeague }
}
