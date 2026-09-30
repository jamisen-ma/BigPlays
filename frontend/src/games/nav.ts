import { useCallback, useEffect, useState } from 'react'
import { isDateKey } from './format'
import type { GamesLeague } from './types'

export type View = 'scores' | 'clips'

export interface NavState {
  view: View
  league: GamesLeague
  /** True when the league came from the URL (vs. the default/remembered one). */
  leagueExplicit: boolean
  /** YYYYMMDD, or null for "today". */
  date: string | null
  game: string | null
}

const LEAGUE_KEY = 'bigplays.scores.league'

function rememberedLeague(): GamesLeague {
  try {
    const v = localStorage.getItem(LEAGUE_KEY)
    if (v === 'nfl' || v === 'mlb') return v
  } catch { /* storage unavailable */ }
  return 'mlb'
}

export function readNav(search = window.location.search): NavState {
  const q = new URLSearchParams(search)
  const game = q.get('game')
  const rawLeague = q.get('league')
  const leagueExplicit = rawLeague === 'nfl' || rawLeague === 'mlb'
  return {
    view: q.get('view') === 'clips' && !game ? 'clips' : 'scores',
    league: leagueExplicit ? rawLeague as GamesLeague : rememberedLeague(),
    leagueExplicit,
    date: isDateKey(q.get('date')) ? q.get('date') : null,
    game: game || null,
  }
}

export function navSearch(s: Pick<NavState, 'view' | 'league' | 'date' | 'game'>): string {
  const q = new URLSearchParams()
  q.set('view', s.view)
  if (s.view === 'scores') {
    q.set('league', s.league)
    if (s.date) q.set('date', s.date)
    if (s.game) q.set('game', s.game)
  }
  return `?${q.toString()}`
}

export type Navigate = (next: Partial<Pick<NavState, 'view' | 'league' | 'date' | 'game'>>, opts?: { replace?: boolean }) => void

/** URL-backed navigation state (?view=scores&league=mlb&date=20260929&game=401907924). */
export function useNav(): [NavState, Navigate] {
  const [state, setState] = useState<NavState>(() => readNav())
  useEffect(() => {
    const onPop = () => setState(readNav())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const navigate = useCallback<Navigate>((next, opts) => {
    setState(prev => {
      const merged: NavState = { ...prev, ...next, leagueExplicit: prev.leagueExplicit || 'league' in next }
      if (next.league) { try { localStorage.setItem(LEAGUE_KEY, next.league) } catch { /* ignore */ } }
      const url = navSearch(merged)
      if (url !== window.location.search) {
        if (opts?.replace) window.history.replaceState(null, '', url)
        else window.history.pushState(null, '', url)
      }
      return merged
    })
  }, [])
  return [state, navigate]
}
