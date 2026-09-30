import { navSearch, type Navigate, type NavState } from './nav'

/** Top-level sections: Scores (play-by-play) and Clips (highlight library). */
export function NavTabs({ nav, navigate }: { nav: NavState; navigate: Navigate }) {
  const tab = (view: NavState['view'], label: string) => (
    <a href={navSearch({ ...nav, view, game: null })} className={nav.view === view ? 'on' : ''}
      aria-current={nav.view === view ? 'page' : undefined}
      onClick={e => { e.preventDefault(); navigate({ view, game: null }) }}>{label}</a>
  )
  return <nav className="view-tabs" aria-label="Sections">{tab('scores', 'Scores')}{tab('clips', 'Clips')}</nav>
}
