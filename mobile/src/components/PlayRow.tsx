// Ported from frontend/src/games/PlayRow.tsx.
import { memo } from 'react'
import { StyleSheet, Text, View } from 'react-native'

import { C, F } from '@/lib/theme'
import { playWhen } from '@/shared/format'
import type { Play, ScoreGame } from '@/shared/types'
import { ClipCard } from './ClipCard'

export const PlayRow = memo(function PlayRow({ play, game, newClip }: { play: Play; game: ScoreGame; newClip: boolean }) {
  const viral = play.clip != null
  const when = playWhen(play, game.league)
  const scorer = play.scoring ? play.team_abbr : null
  return (
    <View style={[s.row, play.scoring && s.scoring, viral && s.viral]} testID={`play-${play.play_id}`}>
      <View style={s.top}>
        <Text style={s.when}>{when}</Text>
        {(play.type || play.scoring || viral) && (
          <View style={s.type}>
            {viral && <Text style={s.viralPill}>🔥 VIRAL</Text>}
            {play.scoring && <Text style={s.scorePill}>SCORE</Text>}
            {!!play.type && <Text style={s.typeText} numberOfLines={1}>{play.type}</Text>}
          </View>
        )}
        {play.away_score != null && play.home_score != null && (
          <View style={s.score} accessibilityLabel="Score after play">
            <Text style={[s.scoreText, scorer === game.away.abbr && s.scored]}>{game.away.abbr} {play.away_score}</Text>
            <Text style={[s.scoreText, scorer === game.home.abbr && s.scored]}>{game.home.abbr} {play.home_score}</Text>
          </View>
        )}
      </View>
      <Text style={[s.text, play.scoring && { color: C.white, fontWeight: '600' }]}>{play.text}</Text>
      {play.clip && <ClipCard clip={play.clip} alternates={play.alternate_clips} league={game.league} isNew={newClip} reason={play.viral_reason} />}
    </View>
  )
})

const s = StyleSheet.create({
  row: { paddingVertical: 10, paddingHorizontal: 12, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.line, borderLeftWidth: 3, borderLeftColor: 'transparent' },
  scoring: { borderLeftColor: C.ok, backgroundColor: 'rgba(52,211,153,0.05)' },
  viral: { borderLeftColor: C.accent, backgroundColor: 'rgba(255,106,26,0.05)' },
  top: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 },
  when: { color: C.muted, fontSize: 11, fontFamily: F.mono, minWidth: 64 },
  type: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6, minWidth: 0 },
  viralPill: { overflow: 'hidden', fontSize: 10, fontWeight: '800', letterSpacing: 0.6, color: C.accent, backgroundColor: C.accentBg, borderWidth: 1, borderColor: C.accentBorder, paddingHorizontal: 5, paddingVertical: 1, borderRadius: 4 },
  scorePill: { overflow: 'hidden', fontSize: 10, fontWeight: '800', letterSpacing: 0.6, color: '#062b1d', backgroundColor: C.ok, paddingHorizontal: 5, paddingVertical: 1, borderRadius: 4 },
  typeText: { color: C.text2, fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5, flexShrink: 1 },
  score: { marginLeft: 'auto', alignItems: 'flex-end' },
  scoreText: { color: C.muted, fontSize: 11, fontFamily: F.mono },
  scored: { color: C.white, fontWeight: '700' },
  text: { color: C.text, fontSize: 14, lineHeight: 20 },
})
