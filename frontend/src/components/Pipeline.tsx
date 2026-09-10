import { useEffect, useRef } from 'react'
import type { CurrentPipeline } from '../useLiveFeed'
import { STAGES, STAGE_LABEL, type LogLine } from '../types'
import { clockTime } from './util'

export function Pipeline({ pipeline, logs, mode, onFire }: { pipeline: CurrentPipeline | null; logs: LogLine[]; mode: 'demo' | 'live' | null; onFire: () => void }) {
  const logRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logs.length])

  const running = pipeline && !pipeline.finished
  const currentStage = pipeline ? STAGES.filter(s => pipeline.stages[s]).at(-1) : undefined

  return (
    <aside className="pipeline">
      <div className="panel-head">
        <h3>Agent pipeline</h3>
        <span className={`status ${running ? 'busy' : 'idle'}`}>{running ? 'EVALUATING' : 'LISTENING'}</span>
      </div>

      <ol className="stages">
        {STAGES.map((s, i) => {
          const ev = pipeline?.stages[s]
          const st = !ev ? 'todo' : ev.status === 'running' ? 'running' : 'done'
          const stale = pipeline?.finished
          return (
            <li key={s} className={`stage ${st} ${stale ? 'stale' : ''}`}>
              <span className="stage-idx">{st === 'done' ? '✓' : st === 'running' ? <span className="spinner" /> : i + 1}</span>
              <div className="stage-body">
                <div className="stage-name">{STAGE_LABEL[s]}</div>
                {ev && <div className="stage-detail">{ev.detail}</div>}
                {s === 'llm' && ev?.status === 'running' && <div className="typing"><i /><i /><i /></div>}
              </div>
            </li>
          )
        })}
      </ol>

      {currentStage && running && (
        <div className="stage-now">Now: <b>{STAGE_LABEL[currentStage]}</b></div>
      )}

      <div className="panel-head small">
        <h3>Agent log</h3>
        {mode === 'demo' && <button className="fire" onClick={onFire}>⚡ Fire next play</button>}
      </div>
      <div className="log" ref={logRef}>
        {logs.map(l => (
          <div key={l.id} className={`log-line ${l.level}`}>
            <span className="log-ts mono">{clockTime(l.ts)}</span>
            <span className="log-msg">{l.msg}</span>
          </div>
        ))}
      </div>
    </aside>
  )
}
