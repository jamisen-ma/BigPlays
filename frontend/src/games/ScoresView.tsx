import { useMemo } from 'react'
import './games.css'
import { useGames } from './api'
import { dateLabel, inputToKey, isDateKey, keyToInput, shiftDate, sortGames, todayKey } from './format'
import { GameCard } from './GameCard'
import { GameDetail } from './GameDetail'
import { navSearch, type Navigate, type NavState } from './nav'
import { NavTabs } from './NavTabs'
import { GAMES_LEAGUES } from './types'

export function ScoresView({ nav, navigate }: { nav: NavState; navigate: Navigate }) {
  return (
    <div className="scores-app">
      <header className="scores-top">
        <span className="logo">BIG<span>PLAYS</span></span>
        <NavTabs nav={nav} navigate={navigate} />
      </header>
      {nav.game
        ? <GameDetail key={nav.game} league={nav.league} gameId={nav.game} allowLeagueFallback={!nav.leagueExplicit}
            onBack={() => navigate({ game: null })}
            onResolvedLeague={league => navigate({ league }, { replace: true })} />
        : <Scoreboard nav={nav} navigate={navigate} />}
    </div>
  )
}

function Scoreboard({ nav, navigate }: { nav: NavState; navigate: Navigate }) {
  const today = todayKey()
  const date = nav.date ?? today
  const { data, error, loading, refresh } = useGames(nav.league, date)
  const games = useMemo(() => sortGames(data?.games ?? []), [data])
  const liveCount = games.filter(g => g.status === 'in').length
  const setDate = (d: string) => navigate({ date: d === today ? null : d })

  return (
    <main className="scoreboard">
      <div className="sb-controls">
        <div className="seg league-tabs" role="tablist" aria-label="League">
          {GAMES_LEAGUES.map(l => (
            <button key={l} role="tab" aria-selected={nav.league === l} className={nav.league === l ? 'on' : ''}
              onClick={() => navigate({ league: l })}>{l.toUpperCase()}</button>
          ))}
        </div>
        <div className="date-picker">
          <button aria-label="Previous day" onClick={() => setDate(shiftDate(date, -1))}>‹</button>
          <label className="date-label">
            <span data-date={date}>{dateLabel(date, today)}</span>
            <input type="date" aria-label="Pick date" value={keyToInput(date)}
              onChange={e => { const k = inputToKey(e.target.value); if (isDateKey(k)) setDate(k) }} />
          </label>
          <button aria-label="Next day" onClick={() => setDate(shiftDate(date, 1))}>›</button>
        </div>
      </div>
      <div className="sb-summary muted">
        {data && <>{games.length} {games.length === 1 ? 'game' : 'games'}{liveCount > 0 && <> · <b className="live-text">{liveCount} live</b></>}</>}
        {error && data && <span className="stale"> · Couldn't refresh, showing last scores</span>}
      </div>

      {!data && loading && <div className="game-grid">{[0, 1, 2, 3].map(i => <div key={i} className="game-card skeleton shimmer" />)}</div>}
      {!data && error && (
        <div className="scores-empty" role="alert">
          <h2>Scores unavailable</h2>
          <p className="muted">{error.status === 404 ? 'The scores service is not available yet.' : `We couldn't load ${nav.league.toUpperCase()} scores (${error.message}).`}</p>
          <button className="retry" onClick={refresh}>Try again</button>
        </div>
      )}
      {data && !games.length && (
        <div className="scores-empty">
          <h2>No {nav.league.toUpperCase()} games</h2>
          <p className="muted">Nothing scheduled for {dateLabel(date, today)}.</p>
        </div>
      )}
      {games.length > 0 && (
        <div className="game-grid">
          {games.map(g => <GameCard key={g.game_id} game={g}
            href={navSearch({ ...nav, game: g.game_id })}
            onOpen={() => { navigate({ game: g.game_id }); window.scrollTo(0, 0) }} />)}
        </div>
      )}
    </main>
  )
}
