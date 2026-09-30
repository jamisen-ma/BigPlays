import { useCallback, useEffect, useRef, useState } from 'react'
import { AppState } from 'react-native'

import { ApiError, POLL_MS, fetchGame, fetchGames, fetchHighlights, poller } from '@/shared/api'
import type { GameDetailResponse, GamesLeague, GamesResponse, Highlight } from '@/shared/types'
import { useApi } from './ApiContext'

export function useAppActive(): boolean {
  const [active, setActive] = useState(AppState.currentState !== 'background')
  useEffect(() => {
    const sub = AppState.addEventListener('change', s => setActive(s === 'active'))
    return () => sub.remove()
  }, [])
  return active
}

export interface Polled<T> {
  data: T | null
  error: ApiError | null
  /** First load in progress (no data yet). */
  loading: boolean
  /** Pull-to-refresh in progress. */
  refreshing: boolean
  refresh: () => void
  base: string
}

/**
 * Load `key` now, then poll every `intervalMs` while `keepPolling(data)` is true and the app is in
 * the foreground. Keeps the last good data on refresh errors (shown as "stale").
 */
function usePolled<T>(
  key: string,
  load: (base: string, signal: AbortSignal) => Promise<T>,
  keepPolling: (data: T) => boolean,
  intervalMs: number,
): Polled<T> {
  const { url: base, ready } = useApi()
  const active = useAppActive()
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const pokeRef = useRef<() => Promise<void>>(async () => {})
  const loadRef = useRef(load)
  loadRef.current = load
  const keepRef = useRef(keepPolling)
  keepRef.current = keepPolling
  const lastKey = useRef<string | null>(null)

  useEffect(() => {
    if (!ready || !active) return
    const fullKey = `${base}|${key}`
    if (lastKey.current !== fullKey) {
      lastKey.current = fullKey
      setData(null); setError(null); setLoading(true)
    }
    const ctrl = new AbortController()
    const p = poller(async () => {
      let live = false
      try {
        const body = await loadRef.current(base, ctrl.signal)
        if (ctrl.signal.aborted) return false
        setData(body); setError(null)
        live = keepRef.current(body)
      } catch (e) {
        if (ctrl.signal.aborted) return false
        setError(e instanceof ApiError ? e : new ApiError(0, 'Network error', true))
        live = true // keep retrying so a flaky connection recovers on its own
      }
      setLoading(false); setRefreshing(false)
      return live
    }, () => intervalMs)
    pokeRef.current = p.poke
    return () => { ctrl.abort(); p.stop() }
  }, [base, key, ready, active, intervalMs])

  const refresh = useCallback(() => { setRefreshing(true); void pokeRef.current() }, [])
  return { data, error, loading: loading && !data, refreshing, refresh, base }
}

export function useGames(league: GamesLeague, date: string): Polled<GamesResponse> {
  return usePolled(`games|${league}|${date}`,
    async (base, signal) => {
      const body = await fetchGames(base, league, date, signal)
      return { ...body, games: Array.isArray(body.games) ? body.games : [] }
    },
    d => d.games.some(g => g.status === 'in'),
    POLL_MS.scores)
}

export function useGameDetail(league: GamesLeague, gameId: string): Polled<GameDetailResponse> & { newClips: Set<string> } {
  const seen = useRef<Map<string, string | null> | null>(null)
  const [newClips, setNewClips] = useState<Set<string>>(new Set())
  useEffect(() => { seen.current = null; setNewClips(new Set()) }, [league, gameId])

  const polled = usePolled(`game|${league}|${gameId}`,
    async (base, signal) => {
      const body = await fetchGame(base, league, gameId, signal)
      const plays = Array.isArray(body.plays) ? body.plays : []
      const clips_unmatched = Array.isArray(body.clips_unmatched) ? body.clips_unmatched : []
      const prev = seen.current
      if (prev) {
        const fresh = plays.filter(p => p.clip && prev.get(p.play_id) !== p.clip.event_id).map(p => p.play_id)
        if (fresh.length) {
          setNewClips(s => new Set([...s, ...fresh]))
          setTimeout(() => setNewClips(s => { const n = new Set(s); fresh.forEach(id => n.delete(id)); return n }), 12_000)
        }
      }
      seen.current = new Map(plays.map(p => [p.play_id, p.clip?.event_id ?? null]))
      return { ...body, plays, clips_unmatched }
    },
    d => d.game?.status === 'in',
    POLL_MS.detail)
  return { ...polled, newClips }
}

export function useHighlights(): Polled<Highlight[]> {
  // The library changes as clips land; refresh every 60 s while the tab is open.
  return usePolled('highlights', (base, signal) => fetchHighlights(base, signal), () => true, 60_000)
}
