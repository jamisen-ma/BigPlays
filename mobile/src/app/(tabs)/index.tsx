// Scores tab: ported from frontend/src/games/ScoresView.tsx (Scoreboard).
import AsyncStorage from '@react-native-async-storage/async-storage'
import { useRouter } from 'expo-router'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { GameCard } from '@/components/GameCard'
import { Logo, Segmented, Skeleton, StateBox } from '@/components/ui'
import { useGames } from '@/lib/hooks'
import { C } from '@/lib/theme'
import { describeError } from '@/shared/api'
import { dateLabel, shiftDate, sortGames, todayKey } from '@/shared/format'
import { GAMES_LEAGUES, type GamesLeague, type ScoreGame } from '@/shared/types'

const LEAGUE_KEY = 'bigplays.scores.league'

export default function ScoresScreen() {
  const insets = useSafeAreaInsets()
  const router = useRouter()
  const [league, setLeagueState] = useState<GamesLeague>('mlb')
  const [today, setToday] = useState(todayKey)
  const [date, setDate] = useState(today)

  useEffect(() => {
    AsyncStorage.getItem(LEAGUE_KEY).then(v => { if (v === 'nfl' || v === 'mlb') setLeagueState(v) }).catch(() => {})
    // roll "today" over at midnight PT
    const t = setInterval(() => setToday(prev => {
      const now = todayKey()
      if (now !== prev) setDate(d => (d === prev ? now : d))
      return now
    }), 60_000)
    return () => clearInterval(t)
  }, [])
  const setLeague = (l: GamesLeague) => { setLeagueState(l); AsyncStorage.setItem(LEAGUE_KEY, l).catch(() => {}) }

  const { data, error, loading, refreshing, refresh, base } = useGames(league, date)
  const games = useMemo(() => sortGames(data?.games ?? []), [data])
  const liveCount = games.filter(g => g.status === 'in').length

  const open = useCallback((g: ScoreGame) => router.push({ pathname: '/game/[league]/[id]', params: { league: g.league, id: g.game_id } }), [router])

  const header = (
    <View>
      <View style={s.top}>
        <Logo />
        {liveCount > 0 && <Text style={s.liveText}>● {liveCount} live</Text>}
      </View>
      <View style={s.controls}>
        <Segmented label="League" value={league} onChange={setLeague}
          options={GAMES_LEAGUES.map(l => ({ value: l, label: l.toUpperCase() }))} />
        <View style={s.datePicker}>
          <Pressable accessibilityLabel="Previous day" onPress={() => setDate(shiftDate(date, -1))} style={s.dateBtn} hitSlop={6}><Text style={s.dateArrow}>‹</Text></Pressable>
          <Pressable onLongPress={() => setDate(today)} onPress={() => setDate(today)} style={s.dateLabelBox} accessibilityLabel="Jump to today">
            <Text style={s.dateLabel} numberOfLines={1}>{dateLabel(date, today)}</Text>
          </Pressable>
          <Pressable accessibilityLabel="Next day" onPress={() => setDate(shiftDate(date, 1))} style={s.dateBtn} hitSlop={6}><Text style={s.dateArrow}>›</Text></Pressable>
        </View>
      </View>
      <Text style={s.summary}>
        {data ? `${games.length} ${games.length === 1 ? 'game' : 'games'}` : ''}
        {data && liveCount > 0 ? <Text style={s.liveText}> · {liveCount} live · updating every 15s</Text> : null}
        {error && data ? <Text style={s.stale}> · Couldn't refresh, showing last scores</Text> : null}
      </Text>
    </View>
  )

  let empty: ReactElement | null = null
  if (!data && loading) empty = <View>{[0, 1, 2, 3].map(i => <Skeleton key={i} height={118} />)}</View>
  else if (!data && error) empty = <StateBox title="Scores unavailable" message={describeError(error, base, `${league.toUpperCase()} scores`)} onRetry={refresh} busy={refreshing} />
  else if (data && !games.length) empty = <StateBox title={`No ${league.toUpperCase()} games`} message={`Nothing scheduled for ${dateLabel(date, today)}.`} />

  return (
    <FlatList
      style={{ backgroundColor: C.bg }}
      contentContainerStyle={{ paddingTop: insets.top + 6, paddingHorizontal: 16, paddingBottom: 24 }}
      data={games}
      keyExtractor={g => g.game_id}
      renderItem={({ item }) => <GameCard game={item} onPress={() => open(item)} />}
      ListHeaderComponent={header}
      ListEmptyComponent={empty}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={C.accent} />}
    />
  )
}

const s = StyleSheet.create({
  top: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', height: 44 },
  controls: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 10 },
  datePicker: { flexDirection: 'row', alignItems: 'center', gap: 2, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 8, padding: 2 },
  dateBtn: { width: 34, height: 32, alignItems: 'center', justifyContent: 'center', borderRadius: 6 },
  dateArrow: { color: C.text2, fontSize: 22, lineHeight: 24 },
  dateLabelBox: { minWidth: 132, alignItems: 'center' },
  dateLabel: { color: C.text, fontWeight: '600', fontSize: 13 },
  summary: { minHeight: 18, marginTop: 10, marginBottom: 8, marginHorizontal: 2, fontSize: 12, color: C.muted },
  liveText: { color: C.live, fontWeight: '700', fontSize: 12 },
  stale: { color: C.warn },
})
