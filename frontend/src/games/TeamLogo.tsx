import { useState } from 'react'
import { teamColor } from './format'
import type { Team } from './types'

export function TeamLogo({ team, size = 28 }: { team: Team; size?: number }) {
  const [failed, setFailed] = useState(false)
  if (!team.logo || failed) {
    return <span className="team-logo fallback" style={{ width: size, height: size, background: teamColor(team) }} aria-hidden>
      {team.abbr?.slice(0, 3)}
    </span>
  }
  return <img className="team-logo" src={team.logo} alt="" width={size} height={size} loading="lazy" onError={() => setFailed(true)} />
}
