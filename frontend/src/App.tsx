import { useEffect, useMemo, useState } from 'react'
import { useLiveFeed } from './useLiveFeed'
import { TopBar } from './components/TopBar'
import { FeedItem } from './components/FeedItem'
import { Player } from './components/Player'
import { Pipeline } from './components/Pipeline'
import { GamesTicker } from './components/GamesTicker'
import { STAGES, STAGE_LABEL } from './types'

export default function App() {
  const feed = useLiveFeed()
  const [selected, setSelected] = useState<string | null>(null)
  const [follow, setFollow] = useState(true)
  const [filter, setFilter] = useState<'all' | 'nba' | 'nfl'>('all')
  const [now, setNow] = useState(Date.now())
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(t) }, [])

  // auto-follow new arrivals
  useEffect(() => {
    if (!feed.lastArrival) return
    if (follow || !selected) setSelected(feed.lastArrival.event_id)
    setToast(feed.lastArrival.title)
    const t = setTimeout(() => setToast(null), 4500)
    return () => clearTimeout(t)
  }, [feed.lastArrival]) // eslint-disable-line react-hooks/exhaustive-deps

  // initial selection once history loads
  useEffect(() => {
    if (!selected && feed.highlights.length) setSelected(feed.highlights[0].event_id)
  }, [feed.highlights.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const visible = useMemo(
    () => feed.highlights.filter(h => filter === 'all' || h.league === filter),
    [feed.highlights, filter],
  )
  const current = feed.highlights.find(h => h.event_id === selected) ?? null
  const pending = feed.pipeline && !feed.pipeline.finished ? feed.pipeline : null
  const pendingStage = pending ? STAGES.filter(s => pending.stages[s]).at(-1) : undefined

  return (
    <div className="app">
      <TopBar
        connected={feed.connected} mode={feed.mode} highlights={feed.highlights} games={feed.games}
        filter={filter} setFilter={setFilter} follow={follow} setFollow={setFollow}
      />
      <main className="layout">
        <aside className="feed">
          <div className="panel-head">
            <h3>Incoming plays</h3>
            <span className="count mono">{visible.length}</span>
          </div>
          <div className="feed-list">
            {pending && (
              <div className="feed-item pending">
                <div className="feed-thumb shimmer" />
                <div className="feed-body">
                  <div className="feed-title muted">Agent evaluating a play…</div>
                  <div className="feed-meta"><span className="spinner" /> {pendingStage ? STAGE_LABEL[pendingStage] : 'ingest'}</div>
                  <div className="stage-detail">{pendingStage ? pending.stages[pendingStage]?.detail : ''}</div>
                </div>
              </div>
            )}
            {visible.map(h => (
              <FeedItem
                key={h.event_id} h={h} now={now}
                active={h.event_id === selected}
                fresh={!!h.ts && (now / 1000 - h.ts) < 30}
                onClick={() => { setSelected(h.event_id); setFollow(false) }}
              />
            ))}
            {!visible.length && !pending && <div className="feed-empty muted">No plays yet. Listening…</div>}
          </div>
        </aside>

        <Player h={current} />

        <Pipeline pipeline={feed.pipeline} logs={feed.logs} mode={feed.mode} onFire={feed.fireNext} />
      </main>
      <GamesTicker games={feed.games} />

      {toast && (
        <div className="toast">
          <span className="toast-k">🔥 VIRAL PLAY DETECTED</span>
          <span className="toast-t">{toast}</span>
        </div>
      )}
    </div>
  )
}
