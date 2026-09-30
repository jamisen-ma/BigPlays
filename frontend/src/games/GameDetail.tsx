import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useGameDetail } from './api'
import { ClipCard } from './ClipCard'
import { groupPlays, teamColor } from './format'
import { StatusPill } from './GameCard'
import { Linescore } from './Linescore'
import { PlayRow } from './PlayRow'
import { SituationView, possessionOf } from './Situation'
import { TeamLogo } from './TeamLogo'
import type { GamesLeague, ScoreGame, Team } from './types'

export function GameDetail({ league, gameId, allowLeagueFallback, onBack, onResolvedLeague }: {
  league: GamesLeague; gameId: string; allowLeagueFallback: boolean
  onBack: () => void; onResolvedLeague: (l: GamesLeague) => void
}) {
  const { data, error, loading, refresh, newPlays, newClips, resolvedLeague } = useGameDetail(league, gameId, allowLeagueFallback)
  const [latestFirst, setLatestFirst] = useState(true)
  const [viralOnly, setViralOnly] = useState(false)

  useEffect(() => { if (resolvedLeague !== league) onResolvedLeague(resolvedLeague) }, [resolvedLeague]) // eslint-disable-line react-hooks/exhaustive-deps

  const game = data?.game
  const plays = data?.plays ?? []
  const viralCount = plays.filter(p => p.clip).length
  const shown = viralOnly ? plays.filter(p => p.clip) : plays
  const groups = useMemo(() => game ? groupPlays(shown, game.league, latestFirst) : [], [shown, game, latestFirst])

  useKeepScroll(data)

  return (
    <main className="game-detail">
      <div className="gd-nav">
        <button className="back" onClick={onBack}>‹ Scores</button>
        {game && <span className="muted">{[game.venue, game.broadcast].filter(Boolean).join(' · ')}</span>}
      </div>

      {!data && loading && <div className="gd-header skeleton shimmer" />}
      {!data && error && (
        <div className="scores-empty" role="alert">
          <h2>Game unavailable</h2>
          <p className="muted">{error.status === 404 ? 'Play-by-play for this game is not available.' : `Couldn't load play-by-play (${error.message}).`}</p>
          <button className="retry" onClick={refresh}>Try again</button>
        </div>
      )}

      {game && data && <>
        <GameHeader game={game} />
        {error && <div className="stale muted">Couldn't refresh. Showing the last update.</div>}
        <Linescore game={game} linescore={data.linescore} />

        <section className="pbp-section" aria-label="Play-by-play">
          <div className="pbp-head">
            <h2>Play-by-play</h2>
            <div className="pbp-controls">
              <button className={`chip-toggle ${viralOnly ? 'on' : ''}`} aria-pressed={viralOnly} onClick={() => setViralOnly(v => !v)}>
                🔥 Viral plays only <span className="mono">{viralCount}</span>
              </button>
              <button className="chip-toggle" aria-label="Toggle play order" onClick={() => setLatestFirst(v => !v)}>
                {latestFirst ? 'Latest first' : 'Oldest first'}
              </button>
            </div>
          </div>
          {!groups.length && <div className="pbp-empty muted">
            {viralOnly ? 'No viral plays yet. Clips get attached here as big plays happen.' : game.status === 'pre' ? 'Play-by-play starts at first pitch / kickoff.' : 'No plays yet.'}
          </div>}
          {groups.map(g => (
            <div key={g.key} className="period-group" data-period={g.label}>
              <h3 className="period-label">{g.label}</h3>
              <ol className="play-list">
                {g.plays.map(p => <PlayRow key={p.play_id} play={p} game={game} isNew={newPlays.has(p.play_id)} newClip={newClips.has(p.play_id)} />)}
              </ol>
            </div>
          ))}
        </section>

        {data.clips_unmatched.length > 0 && (
          <section className="unmatched" aria-label="More clips from this game">
            <h2>More clips from this game</h2>
            <div className="unmatched-grid">
              {data.clips_unmatched.map(c => <ClipCard key={c.event_id} clip={c} league={game.league} compact />)}
            </div>
          </section>
        )}
      </>}
    </main>
  )
}

function HeaderTeam({ team, game, side }: { team: Team; game: ScoreGame; side: 'away' | 'home' }) {
  const pos = possessionOf(game) === team.abbr
  return (
    <div className={`gh-team ${side} ${game.status === 'post' && team.winner === false ? 'loser' : ''}`} style={{ ['--team' as string]: teamColor(team) }}>
      <TeamLogo team={team} size={44} />
      <div className="gh-name">
        <span className="gh-abbr">{team.abbr}{pos && <span className="possession" aria-label={`${team.abbr} ball`}> ●</span>}</span>
        <span className="muted">{team.record ?? ''}</span>
      </div>
      <span className="gh-score mono">{game.status === 'pre' ? '' : team.score ?? '–'}</span>
    </div>
  )
}

function GameHeader({ game }: { game: ScoreGame }) {
  return (
    <section className="gd-header" data-status={game.status} style={{ ['--away' as string]: teamColor(game.away), ['--home' as string]: teamColor(game.home) }}>
      <div className="gh-row">
        <HeaderTeam team={game.away} game={game} side="away" />
        <div className="gh-status"><StatusPill game={game} /></div>
        <HeaderTeam team={game.home} game={game} side="home" />
      </div>
      {game.status === 'in' && game.situation && <div className="gh-situation"><SituationView game={game} /></div>}
      {game.status === 'in' && game.last_play_text && <div className="gh-last muted">{game.last_play_text}</div>}
    </section>
  )
}

/**
 * Keep the reader's place when a poll inserts plays or attaches clips above them. After every
 * commit we record the visible play rows; when new data arrives we find the first recorded row
 * whose own height didn't change and scroll by however far it moved. At the top, new plays simply
 * appear. (Native CSS scroll anchoring is off so every browser behaves the same.)
 */
function useKeepScroll(data: unknown) {
  const anchors = useRef<{ id: string; top: number; height: number }[]>([])
  const lastData = useRef(data)
  const measure = () => {
    const out: { id: string; top: number; height: number }[] = []
    for (const row of document.querySelectorAll<HTMLElement>('.play-row')) {
      const r = row.getBoundingClientRect()
      if (r.bottom < 0) continue
      if (r.top > window.innerHeight) break
      out.push({ id: row.dataset.playId!, top: r.top, height: r.height })
    }
    anchors.current = out
  }
  useEffect(() => {
    const root = document.documentElement
    const prev = root.style.overflowAnchor
    root.style.overflowAnchor = 'none'
    let frame = 0
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; measure() }) }
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll); window.removeEventListener('resize', onScroll)
      cancelAnimationFrame(frame); root.style.overflowAnchor = prev
    }
  }, [])
  useLayoutEffect(() => {
    if (lastData.current !== data) {
      lastData.current = data
      if (window.scrollY > 0) {
        for (const a of anchors.current) {
          const el = document.querySelector<HTMLElement>(`.play-row[data-play-id="${CSS.escape(a.id)}"]`)
          if (!el) continue
          const r = el.getBoundingClientRect()
          if (Math.abs(r.height - a.height) > 1) continue
          if (Math.abs(r.top - a.top) > 1) window.scrollBy(0, r.top - a.top)
          break
        }
      }
    }
    measure()
  })
}
