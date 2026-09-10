import { useCallback, useEffect, useRef, useState } from 'react'
import type { Game, Highlight, LogLine, PipelineEvent, Stage } from './types'

export interface CurrentPipeline {
  play_id: string
  stages: Partial<Record<Stage, PipelineEvent>>
  startedAt: number
  finished: boolean
}

export interface LiveState {
  connected: boolean
  mode: 'demo' | 'live' | null
  games: Game[]
  highlights: Highlight[]
  pipeline: CurrentPipeline | null
  logs: LogLine[]
  lastArrival: Highlight | null
}

const MAX_LOGS = 120

export function useLiveFeed() {
  const [state, setState] = useState<LiveState>({
    connected: false, mode: null, games: [], highlights: [], pipeline: null, logs: [], lastArrival: null,
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
      setState(s => ({ ...s, mode: d.mode, games: d.games ?? [], highlights: d.recent ?? [], connected: true }))
      log('info', `connected · mode=${d.mode} · ${d.recent?.length ?? 0} highlights on disk`)
    })

    es.addEventListener('game_tick', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      setState(s => ({ ...s, games: d.games ?? [] }))
    })

    es.addEventListener('pipeline', (e) => {
      const d = JSON.parse((e as MessageEvent).data) as PipelineEvent
      setState(s => {
        const cur = s.pipeline && s.pipeline.play_id === d.play_id && !s.pipeline.finished
          ? s.pipeline
          : { play_id: d.play_id, stages: {}, startedAt: d.ts, finished: false }
        const next: CurrentPipeline = { ...cur, stages: { ...cur.stages, [d.stage]: d } }
        return { ...s, pipeline: next }
      })
      if (d.status === 'running') log('stage', `[${d.stage}] ${d.detail}`, d.ts)
    })

    es.addEventListener('highlight', (e) => {
      const h = JSON.parse((e as MessageEvent).data) as Highlight
      setState(s => {
        if (s.highlights.some(x => x.event_id === h.event_id)) return s
        return {
          ...s,
          highlights: [h, ...s.highlights].slice(0, 200),
          lastArrival: h,
          pipeline: s.pipeline ? { ...s.pipeline, finished: true } : s.pipeline,
        }
      })
      log('highlight', `VIRAL · ${h.title} (hype ${(h.llm?.hype_score ?? h.combined_score).toFixed(2)})`, h.ts)
    })

    es.addEventListener('log', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      log(d.level ?? 'info', d.msg, d.ts)
    })

    return () => es.close()
  }, [log])

  const fireNext = useCallback(async () => {
    await fetch('/api/demo/next', { method: 'POST' })
  }, [])

  return { ...state, fireNext }
}
