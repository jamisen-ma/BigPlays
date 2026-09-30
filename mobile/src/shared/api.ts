// Ported from frontend/src/games/api.ts (fetch + poller), minus React and SSE. Pure TS.
import type { GameDetailResponse, GamesLeague, GamesResponse, Highlight } from './types'

export class ApiError extends Error {
  /** status 0 = network failure / timeout / invalid body. */
  constructor(public status: number, message: string, public network = false) { super(message) }
}

const TIMEOUT_MS = 10_000

/** Fetch JSON; non-2xx and `{ok:false, error}` bodies become ApiError (status + server message). */
export async function getJson<T>(base: string, path: string, signal?: AbortSignal, timeoutMs = TIMEOUT_MS): Promise<T> {
  const ctrl = new AbortController()
  const onAbort = () => ctrl.abort()
  signal?.addEventListener('abort', onAbort)
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  let res: Response
  try {
    res = await fetch(`${base}${path}`, { headers: { accept: 'application/json' }, signal: ctrl.signal })
  } catch {
    throw new ApiError(0, signal?.aborted ? 'Aborted' : 'Network error', true)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
  let body: (T & { ok?: boolean; error?: string; detail?: string }) | null = null
  try { body = await res.json() } catch { /* non-JSON (e.g. an HTML 404) */ }
  if (!res.ok || body == null || (typeof body === 'object' && !Array.isArray(body) && body.ok === false)) {
    const message = body?.error || (typeof body?.detail === 'string' ? body.detail : '')
      || (res.status === 404 ? 'Not found' : res.ok ? 'Invalid response' : `HTTP ${res.status}`)
    throw new ApiError(res.ok ? 0 : res.status, message)
  }
  return body
}

export const gamesPath = (league: GamesLeague, date: string) => `/api/games?league=${league}&date=${date}`
export const gamePath = (league: GamesLeague, gameId: string) => `/api/games/${league}/${encodeURIComponent(gameId)}`
export const HIGHLIGHTS_PATH = '/api/highlights'

export const fetchGames = (base: string, league: GamesLeague, date: string, signal?: AbortSignal) =>
  getJson<GamesResponse>(base, gamesPath(league, date), signal)
export const fetchGame = (base: string, league: GamesLeague, gameId: string, signal?: AbortSignal) =>
  getJson<GameDetailResponse>(base, gamePath(league, gameId), signal)

/** /api/highlights returns a bare array today; accept `{highlights|items: [...]}` too. */
export async function fetchHighlights(base: string, signal?: AbortSignal): Promise<Highlight[]> {
  const body = await getJson<unknown>(base, HIGHLIGHTS_PATH, signal)
  if (Array.isArray(body)) return body as Highlight[]
  const o = body as { highlights?: Highlight[]; items?: Highlight[] }
  return o.highlights ?? o.items ?? []
}

/** Human message for an error screen. */
export function describeError(e: unknown, base: string, what = 'data'): string {
  if (e instanceof ApiError) {
    if (e.network) return `Can't reach BigPlays at ${base}. Check that the backend is running on a reachable address (not 127.0.0.1) and that this device is on the same network or tailnet.`
    if (e.status === 404) return `The ${what} isn't available (404).`
    return `Couldn't load ${what} (${e.message}).`
  }
  return `Couldn't load ${what}.`
}

/**
 * Run `load` now, then every `interval` ms while it returns true; `poke()` runs it immediately
 * (coalescing with an in-flight run).
 */
export function poller(load: () => Promise<boolean>, interval: () => number) {
  let timer: ReturnType<typeof setTimeout> | undefined
  let running = false, again = false, stopped = false
  const run = async (): Promise<void> => {
    if (stopped) return
    if (running) { again = true; return }
    running = true
    clearTimeout(timer)
    let keepGoing = false
    try { keepGoing = await load() } catch { keepGoing = false }
    running = false
    if (stopped) return
    if (again) { again = false; return run() }
    if (keepGoing) timer = setTimeout(run, interval())
  }
  void run()
  return { poke: run, stop: () => { stopped = true; clearTimeout(timer) } }
}

export const POLL_MS = { scores: 15_000, detail: 8_000 } as const
