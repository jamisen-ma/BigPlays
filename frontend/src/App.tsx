import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useLiveFeed } from './useLiveFeed'
import { TopBar } from './components/TopBar'
import { FeedItem } from './components/FeedItem'
import { LiveStream } from './components/LiveStream'
import { AgentMonitor } from './components/AgentMonitor'
import { Player } from './components/Player'
import { Pipeline } from './components/Pipeline'
import { GamesTicker } from './components/GamesTicker'
import { MLBGames } from './components/MLBGames'
import { SocialFeed } from './components/SocialFeed'
import { sourceKindOf, sourceLabel } from './components/util'
import { SOURCE_KINDS, type League, type SourceKind } from './types'
import { useNav } from './games/nav'
import { NavTabs } from './games/NavTabs'
import { ScoresView } from './games/ScoresView'

/** Top-level navigation: Scores (default, play-by-play) and Clips (highlight library). */
export default function App() {
  const [nav, navigate] = useNav()
  if (nav.view === 'scores') return <ScoresView nav={nav} navigate={navigate} />
  return <ClipsApp nav={<NavTabs nav={nav} navigate={navigate} />} />
}

function ClipsApp({ nav }: { nav: ReactNode }) {
  const feed = useLiveFeed()
  const requestedLeague = new URLSearchParams(window.location.search).get('league')
  const initialLeague: 'all' | League = (['nba', 'nfl', 'ncaaf', 'mlb'] as string[]).includes(requestedLeague ?? '')
    ? requestedLeague as League : 'all'
  const [selected, setSelected] = useState<string | null>(null)
  const [follow, setFollow] = useState(initialLeague === 'all')
  const [filter, setFilter] = useState<'all' | League>(initialLeague)
  const [ranked, setRanked] = useState(false)
  const [query, setQuery] = useState('')
  const [game, setGame] = useState('all')
  const [source, setSource] = useState<'all' | SourceKind>('all')
  const [now, setNow] = useState(Date.now())
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(t) }, [])

  // auto-follow new arrivals
  useEffect(() => {
    if (!feed.lastArrival) return
    if (filter !== 'all' && feed.lastArrival.league !== filter) return
    if ((follow || !selected) && (filter === 'all' || feed.lastArrival.league === filter)) setSelected(feed.lastArrival.event_id)
    setToast(feed.lastArrival.title)
    const t = setTimeout(() => setToast(null), 4500)
    return () => clearTimeout(t)
  }, [feed.lastArrival]) // eslint-disable-line react-hooks/exhaustive-deps

  // initial selection once history loads
  useEffect(() => {
    const first = feed.highlights.find(h => filter === 'all' || h.league === filter)
    if (!selected && first) setSelected(first.event_id)
  }, [feed.highlights.length, filter]) // eslint-disable-line react-hooks/exhaustive-deps

  const libraryGames = useMemo(() => Array.from(new Map(feed.highlights
    .filter(h => filter === 'all' || h.league === filter)
    .map(h => [h.game_id, { id: h.game_id, label: `${h.away ?? ''} @ ${h.home ?? ''}` }])).values())
    .sort((a, b) => a.label.localeCompare(b.label)), [feed.highlights, filter])
  const hasArchive = feed.highlights.some(h => h.imported)
  const visible = useMemo(
    () => {
      const words = query.toLowerCase().trim().split(/\s+/).filter(Boolean)
      const items = feed.highlights.filter(h => {
        const searchable = [h.title, h.description, h.player, h.away, h.home, ...(h.tags ?? [])].join(' ').toLowerCase()
        return (filter === 'all' || h.league === filter) && (game === 'all' || h.game_id === game)
          && (source === 'all' || sourceKindOf(h) === source)
          && words.every(word => searchable.includes(word))
      })
      return ranked ? items.sort((a, b) => (b.combined_score ?? 0) - (a.combined_score ?? 0)) : items
    },
    [feed.highlights, filter, game, query, ranked, source],
  )
  const sourceCounts = useMemo(() => {
    const counts: Record<SourceKind, number> = { live_capture: 0, official_upload: 0 }
    for (const h of feed.highlights) if (filter === 'all' || h.league === filter) counts[sourceKindOf(h)]++
    return counts
  }, [feed.highlights, filter])
  const arrivalKind = feed.lastArrival ? sourceKindOf(feed.lastArrival) : null
  const current = feed.highlights.find(h => h.event_id === selected) ?? null

  return (
    <div className="app">
      <TopBar
        nav={nav}
        connected={feed.connected} highlights={feed.highlights} games={feed.games}
        filter={filter} setFilter={value => { setFilter(value); setGame('all'); setFollow(false) }} follow={follow} setFollow={setFollow}
      />
      <main className="layout">
        <aside className="feed">
          <div className="panel-head">
            <h3>{hasArchive ? 'Highlight library' : 'Incoming plays'}</h3>
            <button onClick={() => setRanked(!ranked)}>{ranked ? 'Sort: highlight score' : 'Sort: newest'}</button>
            <span className="count mono" aria-label="Visible highlights">{visible.length}</span>
          </div>
          <div className="library-filters">
            <input type="search" aria-label="Search highlights" placeholder="Search player, team or play" value={query}
              onChange={e => { setQuery(e.target.value); setFollow(false) }} />
            <select aria-label="Filter by game" value={game} onChange={e => { setGame(e.target.value); setFollow(false) }}>
              <option value="all">All games ({libraryGames.length})</option>
              {libraryGames.map(g => <option key={g.id} value={g.id}>{g.label}</option>)}
            </select>
            <select aria-label="Filter by source" value={source} onChange={e => { setSource(e.target.value as 'all' | SourceKind); setFollow(false) }}>
              <option value="all">All sources</option>
              {SOURCE_KINDS.map(kind => <option key={kind} value={kind}>
                {sourceLabel(kind)} ({sourceCounts[kind]})
              </option>)}
            </select>
          </div>
          <div className="feed-list">
            {visible.map(h => (
              <FeedItem
                key={h.event_id} h={h} now={now}
                active={h.event_id === selected}
                fresh={arrivedWithin(h, now, 30)}
                onClick={() => { setSelected(h.event_id); setFollow(false) }}
              />
            ))}
            {!visible.length && <div className="feed-empty muted">{query || game !== 'all' || source !== 'all' ? 'No highlights match these filters.' : 'No plays yet. Listening…'}</div>}
          </div>
        </aside>

        <div className="watch-column">
          <MLBGames highlights={feed.highlights} onSelect={id => {
            setFilter('mlb'); setGame(id); setQuery(''); setFollow(false)
            const highlight = feed.highlights.find(h => h.league === 'mlb' && h.game_id === id)
            if (highlight) setSelected(highlight.event_id)
          }} />
          <AgentMonitor />
          <LiveStream />
          <Player h={current} />
          <SocialFeed league={filter === 'mlb' ? 'mlb' : filter === 'nfl' ? 'nfl' : current?.league === 'mlb' ? 'mlb' : 'nfl'}
            currentClipId={selected} clipTitle={id => feed.highlights.find(h => h.event_id === id)?.title}
            onSelectClip={id => { setSelected(id); setFollow(false) }} />
        </div>

        <Pipeline logs={feed.logs} />
      </main>
      <GamesTicker games={feed.games} />

      {toast && (
        <div className="toast">
          <span className="toast-k">🔥 {arrivalKind === 'live_capture' ? 'NEW LIVE CAPTURE'
            : arrivalKind === 'official_upload' ? 'NEW OFFICIAL UPLOAD' : 'REPLAY HIGHLIGHT INCOMING'}</span>
          <span className="toast-t">{toast}</span>
        </div>
      )}
    </div>
  )
}

function arrivedWithin(h: { ts?: number; received_utc?: string }, now: number, seconds: number): boolean {
  const at = h.ts ?? (h.received_utc ? Date.parse(h.received_utc) / 1000 : NaN)
  return Number.isFinite(at) && now / 1000 - at < seconds
}
