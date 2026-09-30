// Ported from frontend/src/games/GameCard.tsx.
import { memo } from 'react'
import { Pressable, StyleSheet, Text, View } from 'react-native'

import { C, F, RADIUS } from '@/lib/theme'
import { possessionOf, statusText } from '@/shared/format'
import type { ScoreGame, Team } from '@/shared/types'
import { SituationView } from './Situation'
import { ClipBadge, StatusPill, TeamLogo } from './ui'

function TeamRow({ team, game, possession }: { team: Team; game: ScoreGame; possession: boolean }) {
  const loser = game.status === 'post' && team.winner === false
  return (
    <View style={[s.team, loser && { opacity: 0.55 }]}>
      <TeamLogo team={team} size={28} />
      <Text style={s.abbr}>{team.abbr}</Text>
      {possession && <Text style={s.possession} accessibilityLabel={`${team.abbr} ball`}>●</Text>}
      <Text style={s.record} numberOfLines={1}>{team.record ?? ''}</Text>
      <Text style={[s.score, team.winner && { color: C.white }]}>{game.status === 'pre' ? '' : team.score ?? '–'}</Text>
    </View>
  )
}

export const GameCard = memo(function GameCard({ game, onPress }: { game: ScoreGame; onPress: () => void }) {
  const pos = possessionOf(game)
  const hasSituation = game.status === 'in' && !!game.situation
  return (
    <Pressable onPress={onPress} accessibilityRole="button" testID={`game-${game.game_id}`}
      accessibilityLabel={`${game.away.abbr} at ${game.home.abbr}, ${statusText(game)}`}
      style={({ pressed }) => [s.card, game.status === 'in' && s.cardLive, pressed && { transform: [{ scale: 0.99 }], borderColor: C.line2 }]}>
      <View style={s.head}>
        <StatusPill game={game} />
        <Text style={s.broadcast} numberOfLines={1}>{game.broadcast ?? ''}</Text>
        <ClipBadge count={game.clip_count} />
      </View>
      <View style={s.body}>
        <View style={s.teams}>
          <TeamRow team={game.away} game={game} possession={!!pos && pos === game.away.abbr} />
          <TeamRow team={game.home} game={game} possession={!!pos && pos === game.home.abbr} />
        </View>
        {hasSituation && <View style={s.situation}><SituationView game={game} compact /></View>}
      </View>
      {game.status === 'in' && !!game.last_play_text && <Text style={s.last} numberOfLines={2}>{game.last_play_text}</Text>}
      {game.status === 'pre' && !!game.venue && <Text style={s.last} numberOfLines={1}>{game.venue}</Text>}
    </Pressable>
  )
})

const s = StyleSheet.create({
  card: { paddingTop: 10, paddingHorizontal: 12, paddingBottom: 12, borderRadius: RADIUS, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, marginBottom: 10 },
  cardLive: { borderColor: 'rgba(255,59,71,0.35)' },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 },
  broadcast: { flex: 1, color: C.muted, fontSize: 11 },
  body: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  teams: { flex: 1, minWidth: 0, gap: 4 },
  team: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  abbr: { color: C.text, fontWeight: '800', fontSize: 20, letterSpacing: 0.5, fontFamily: F.display, minWidth: 44 },
  possession: { color: C.warn, fontSize: 10 },
  record: { flex: 1, color: C.muted, fontSize: 11 },
  score: { color: C.text, fontSize: 22, fontWeight: '700', minWidth: 28, textAlign: 'right', fontFamily: F.display, fontVariant: ['tabular-nums'] },
  situation: { paddingLeft: 10, borderLeftWidth: 1, borderLeftColor: C.line },
  last: { marginTop: 8, fontSize: 12, lineHeight: 16, color: C.muted },
})
