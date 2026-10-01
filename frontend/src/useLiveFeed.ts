import { useCallback, useEffect, useRef, useState } from 'react'
import type { Game, Highlight, LogLine } from './types'

export interface LiveState {
  connected: boolean
  games: Game[]
  highlights: Highlight[]
  logs: LogLine[]
  lastArrival: Highlight | null
}

const MAX_LOGS = 120

/**
 * Merge the server's saved history (hello.recent, newest first) with clips already on screen.
 * The server list is authoritative for records it knows about; clips that arrived live in this
 * session but are not (yet) in the snapshot are kept on top so a reconnect never drops them.
 * Every clip appears exactly once, keyed by event_id.
 */
export function mergeHistory(current: Highlight[], recent: Highlight[]): Highlight[] {
  const seen = new Set<string>()
  const server: Highlight[] = []
  for (const h of recent) {
    if (!h?.event_id || seen.has(h.event_id)) continue
    seen.add(h.event_id)
    server.push(h)
  }
  const sessionOnly = current.filter(h => !seen.has(h.event_id) && (seen.add(h.event_id), true))
  return [...sessionOnly, ...server]
}

export function useLiveFeed() {
  const [state, setState] = useState<LiveState>({
    connected: false, games: [], highlights: [], logs: [], lastArrival: null,
  })
  const logId = useRef(0)

  const log = useCallback((level: LogLine['level'], msg: string, ts = Date.now() / 1000) => {
    setState(s => ({ ...s, logs: [...s.logs.slice(-MAX_LOGS + 1), { id: ++logId.current, ts, level, msg }] }))
  }, [])

  useEffect(() => {
    const es = new EventSource('/api/stream')

    es.onopen = () => setState(s => ({ ...s, connected: true }))
    es.onerror = () => setState(s => ({ ...s, connected: false }))

    es.addEventListener('hello', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      setState(s => {
        const highlights = mergeHistory(s.highlights, d.recent ?? [])
        // On a reconnect, clips that landed while we were disconnected are real arrivals too.
        const known = new Set(s.highlights.map(h => h.event_id))
        const missed = s.highlights.length ? highlights.find(h => !known.has(h.event_id)) : undefined
        return { ...s, games: d.games ?? [], highlights, connected: true, lastArrival: missed ?? s.lastArrival }
      })
      log('info', `connected · mode=${d.mode} · ${d.recent?.length ?? 0} highlights on disk`)
    })

    es.addEventListener('game_tick', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      setState(s => ({ ...s, games: d.games ?? [] }))
    })

    es.addEventListener('highlight', (e) => {
      const h = JSON.parse((e as MessageEvent).data) as Highlight
      if (!h?.event_id) return
      setState(s => {
        const known = s.highlights.some(x => x.event_id === h.event_id)
        // Anything we have already seen (e.g. a replayed event after reconnect) is
        // refreshed in place rather than duplicated or re-announced.
        if (known) {
          return { ...s, highlights: s.highlights.map(old => old.event_id === h.event_id ? { ...old, ...h } : old) }
        }
        return {
          ...s,
          highlights: [h, ...s.highlights.filter(old => old.event_id !== h.event_id)],
          lastArrival: h,
        }
      })
    })

    es.addEventListener('log', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      log(d.level ?? 'info', d.msg, d.ts)
    })

    es.addEventListener('highlight_update', (e) => {
      const h = JSON.parse((e as MessageEvent).data) as Highlight
      if (!h?.event_id) return
      setState(s => s.highlights.some(old => old.event_id === h.event_id)
        ? { ...s, highlights: s.highlights.map(old => old.event_id === h.event_id ? h : old) }
        : { ...s, highlights: [h, ...s.highlights] })
    })

    return () => es.close()
  }, [log])

  // Announce each genuinely new arrival once (duplicates never update lastArrival).
  const arrival = state.lastArrival
  useEffect(() => {
    if (arrival) log('highlight', `VIRAL · ${arrival.title} (hype ${(arrival.llm?.hype_score ?? arrival.combined_score ?? 0).toFixed(2)})`, arrival.ts)
  }, [arrival, log])

  return state
}
