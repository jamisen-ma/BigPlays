// Game screen: ported from frontend/src/games/GameDetail.tsx.
import { Stack, useLocalSearchParams } from 'expo-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { RefreshControl, SectionList, StyleSheet, Text, View, type ViewToken } from 'react-native'

import { ClipCard } from '@/components/ClipCard'
import { Linescore } from '@/components/Linescore'
import { PlayRow } from '@/components/PlayRow'
import { SituationView } from '@/components/Situation'
import { Chip, Skeleton, StateBox, StatusPill, TeamLogo } from '@/components/ui'
import * as autoplay from '@/lib/autoplay'
import { useGameDetail } from '@/lib/hooks'
import { C, F, RADIUS } from '@/lib/theme'
import { describeError } from '@/shared/api'
import { groupPlays, possessionOf, teamColor, viralPlays } from '@/shared/format'
import type { GamesLeague, Play, ScoreGame, Team } from '@/shared/types'

export default function GameScreen() {
  const params = useLocalSearchParams<{ league: string; id: string }>()
  const league: GamesLeague = params.league === 'nfl' ? 'nfl' : 'mlb'
  const gameId = String(params.id ?? '')
  const { data, error, loading, refreshing, refresh, base, newClips } = useGameDetail(league, gameId)
  const [latestFirst, setLatestFirst] = useState(true)
  const [viralOnly, setViralOnly] = useState(false)
  const viewability = useAutoplayViewability()

  const game = data?.game
  const plays = data?.plays ?? []
  const viral = useMemo(() => viralPlays(plays), [plays])
  const sections = useMemo(() => game
    ? groupPlays(viralOnly ? viral : plays, game.league, latestFirst).map(g => ({ key: g.key, title: g.label, data: g.plays }))
    : [], [plays, viral, viralOnly, game, latestFirst])

  const title = game ? `${game.away.abbr} @ ${game.home.abbr}` : 'Game'

  if (!data) {
    return (
      <View style={s.screen}>
        <Stack.Screen options={{ title }} />
        <View style={{ padding: 16 }}>
          {loading ? <><Skeleton height={130} /><Skeleton height={80} /><Skeleton height={300} /></>
            : <StateBox title="Game unavailable" onRetry={refresh} busy={refreshing}
                message={error?.status === 404 ? 'Play-by-play for this game is not available.' : describeError(error, base, 'play-by-play')} />}
        </View>
      </View>
    )
  }

  const g = data.game
  const header = (
    <View style={{ paddingHorizontal: 16, paddingTop: 8 }}>
      {!!(g.venue || g.broadcast) && <Text style={s.venue}>{[g.venue, g.broadcast].filter(Boolean).join(' · ')}</Text>}
      <GameHeader game={g} />
      {error && <Text style={s.stale}>Couldn't refresh. Showing the last update.</Text>}
      <Linescore game={g} linescore={data.linescore} />
      <View style={s.pbpHead}>
        <Text style={s.h2}>Play-by-play</Text>
        {g.status === 'in' && <Text style={s.liveHint}>● live · 8s</Text>}
      </View>
      <View style={s.controls}>
        <Chip on={viralOnly} onPress={() => setViralOnly(v => !v)}>🔥 Viral only  {viral.length}</Chip>
        <Chip onPress={() => setLatestFirst(v => !v)}>{latestFirst ? 'Latest first' : 'Oldest first'}</Chip>
      </View>
    </View>
  )

  const footer = data.clips_unmatched.length > 0 ? (
    <View style={s.unmatched} accessibilityLabel="More clips from this game">
      <Text style={s.h2}>More clips from this game</Text>
      {data.clips_unmatched.map(c => <ClipCard key={c.event_id} clip={c} league={g.league} compact />)}
    </View>
  ) : <View style={{ height: 24 }} />

  return (
    <View style={s.screen}>
      <Stack.Screen options={{ title }} />
      <SectionList
        sections={sections}
        keyExtractor={(p: Play) => p.play_id}
        renderItem={({ item }) => <PlayRow play={item} game={g} newClip={newClips.has(item.play_id)} />}
        renderSectionHeader={({ section }) => <Text style={s.period}>{section.title}</Text>}
        stickySectionHeadersEnabled
        ListHeaderComponent={header}
        ListFooterComponent={footer}
        ListEmptyComponent={<Text style={s.empty}>
          {viralOnly ? 'No viral plays yet. Clips get attached here as big plays happen.'
            : g.status === 'pre' ? 'Play-by-play starts at first pitch / kickoff.' : 'No plays yet.'}
        </Text>}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={C.accent} />}
        viewabilityConfigCallbackPairs={viewability}
        initialNumToRender={20}
        windowSize={11}
      />
    </View>
  )
}

/**
 * Instagram-style autoplay on native: report which play rows are on screen (>= 60% to start a clip,
 * >= 25% to keep it playing, any part for one the viewer started) to lib/autoplay.ts.
 * Web uses an IntersectionObserver per clip instead (list viewability goes stale there).
 */
function useAutoplayViewability() {
  const pairs = useRef((['start', 'keep', 'any'] as const).map(level => ({
    viewabilityConfig: {
      itemVisiblePercentThreshold: 100 * (level === 'start' ? autoplay.START : level === 'keep' ? autoplay.KEEP : autoplay.ANY),
      minimumViewTime: level === 'start' ? 150 : 0,
    },
    onViewableItemsChanged: ({ viewableItems }: { viewableItems: ViewToken[] }) =>
      autoplay.setVisible(level, viewableItems.map(v => (v.item as Partial<Play> | null)?.play_id).filter((id): id is string => !!id)),
  }))).current
  useEffect(() => () => { for (const level of ['start', 'keep', 'any'] as const) autoplay.setVisible(level, []) }, [])
  return autoplay.usesIntersectionObserver ? undefined : pairs
}

function HeaderTeam({ team, game }: { team: Team; game: ScoreGame }) {
  const pos = possessionOf(game) === team.abbr
  const loser = game.status === 'post' && team.winner === false
  return (
    <View style={[s.hTeam, loser && { opacity: 0.55 }]}>
      <TeamLogo team={team} size={44} />
      <Text style={s.hAbbr}>{team.abbr}{pos ? <Text style={{ color: C.warn, fontSize: 12 }}> ●</Text> : null}</Text>
      <Text style={s.hRecord}>{team.record ?? ''}</Text>
    </View>
  )
}

function GameHeader({ game }: { game: ScoreGame }) {
  const score = (t: Team) => (game.status === 'pre' ? '' : t.score ?? '–')
  return (
    <View style={s.header}>
      <View style={[s.band, { left: 0, backgroundColor: teamColor(game.away) }]} />
      <View style={[s.band, { right: 0, backgroundColor: teamColor(game.home) }]} />
      <View style={s.hRow}>
        <HeaderTeam team={game.away} game={game} />
        <Text style={[s.hScore, game.away.winner && { color: C.white }]}>{score(game.away)}</Text>
        <View style={s.hStatus}><StatusPill game={game} /></View>
        <Text style={[s.hScore, game.home.winner && { color: C.white }]}>{score(game.home)}</Text>
        <HeaderTeam team={game.home} game={game} />
      </View>
      {game.status === 'in' && !!game.situation && <View style={s.hSituation}><SituationView game={game} /></View>}
      {game.status === 'in' && !!game.last_play_text && <Text style={s.hLast}>{game.last_play_text}</Text>}
    </View>
  )
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg },
  venue: { color: C.muted, fontSize: 12, marginBottom: 8 },
  stale: { color: C.warn, fontSize: 12, marginTop: 6 },
  header: { paddingVertical: 14, paddingHorizontal: 12, borderRadius: RADIUS, borderWidth: 1, borderColor: C.line, backgroundColor: C.panel, overflow: 'hidden' },
  band: { position: 'absolute', top: 0, bottom: 0, width: '32%', opacity: 0.18 },
  hRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  hTeam: { alignItems: 'center', width: 64, gap: 2 },
  hAbbr: { color: C.text, fontWeight: '800', fontSize: 18, fontFamily: F.display },
  hRecord: { color: C.muted, fontSize: 11 },
  hScore: { color: C.text2, fontSize: 38, fontWeight: '800', fontFamily: F.display, minWidth: 44, textAlign: 'center', fontVariant: ['tabular-nums'] },
  hStatus: { flex: 1, alignItems: 'center' },
  hSituation: { marginTop: 12, alignItems: 'center' },
  hLast: { marginTop: 10, color: C.text2, fontSize: 12, lineHeight: 17, textAlign: 'center' },
  pbpHead: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginTop: 18 },
  h2: { color: C.text, fontSize: 22, fontWeight: '800', fontFamily: F.display, marginBottom: 4 },
  liveHint: { color: C.live, fontSize: 11, fontWeight: '700' },
  controls: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 6, marginBottom: 8 },
  period: { backgroundColor: C.bg2, color: C.text2, fontWeight: '800', fontSize: 12, letterSpacing: 1, textTransform: 'uppercase', paddingVertical: 6, paddingHorizontal: 16, borderTopWidth: 1, borderBottomWidth: 1, borderColor: C.line },
  empty: { color: C.muted, textAlign: 'center', padding: 24, fontSize: 13 },
  unmatched: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 32 },
})
