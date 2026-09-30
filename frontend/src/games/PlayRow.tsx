import { memo } from 'react'
import { ClipCard } from './ClipCard'
import { playWhen } from './format'
import type { GamesLeague, Play, ScoreGame } from './types'

export const PlayRow = memo(function PlayRow({ play, game, isNew, newClip }: {
  play: Play; game: ScoreGame; isNew: boolean; newClip: boolean
}) {
  const league = game.league as GamesLeague
  const viral = play.clip != null
  const when = playWhen(play, league)
  const scorer = play.scoring ? play.team_abbr : null
  const cls = ['play-row', play.scoring && 'scoring', viral && 'viral', play.is_key_play && 'key', isNew && 'is-new'].filter(Boolean).join(' ')
  return (
    <li className={cls} data-play-id={play.play_id} data-scoring={play.scoring ? 'true' : undefined}>
      <div className="play-when mono">{when}</div>
      <div className="play-main">
        {(play.type || play.scoring || viral) && (
          <div className="play-type">
            {viral && <span className="viral-pill">🔥 VIRAL</span>}
            {play.scoring && <span className="score-pill">SCORE</span>}
            {play.type && <span>{play.type}</span>}
          </div>
        )}
        <p className="play-text">{play.text}</p>
        {play.clip && <ClipCard clip={play.clip} alternates={play.alternate_clips} league={league} isNew={newClip} reason={play.viral_reason} />}
      </div>
      <div className="play-score mono" aria-label="Score after play">
        {play.away_score != null && play.home_score != null ? <>
          <span className={scorer === game.away.abbr ? 'scored' : ''}>{game.away.abbr} {play.away_score}</span>
          <span className={scorer === game.home.abbr ? 'scored' : ''}>{game.home.abbr} {play.home_score}</span>
        </> : null}
      </div>
    </li>
  )
})
