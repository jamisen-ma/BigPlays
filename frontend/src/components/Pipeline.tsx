import { useEffect, useRef } from 'react'
import type { LogLine } from '../types'
import { clockTime } from './util'

export function Pipeline({ logs }: { logs: LogLine[] }) {
  const logRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logs.length])

  return (
    <aside className="pipeline">
      <div className="panel-head">
        <h3>Agent log</h3>
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
