// Clips tab: the /api/highlights library.
import { Image } from 'expo-image'
import { useRouter } from 'expo-router'
import { memo, useMemo, useState } from 'react'
import { FlatList, Linking, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { SourcePill } from '@/components/ClipCard'
import { fillFrame, MEDIA_MAX_WIDTH, MediaFrame } from '@/components/MediaFrame'
import { Chip, Logo, Skeleton, StateBox } from '@/components/ui'
import { useApi } from '@/lib/ApiContext'
import { setSoundOn } from '@/lib/autoplay'
import { useHighlights } from '@/lib/hooks'
import { C, F, RADIUS } from '@/lib/theme'
import { describeError } from '@/shared/api'
import { absUrl } from '@/shared/apiBase'
import { formatDuration } from '@/shared/format'
import { filterLibrary, highlightPoster, highlightVideo, libraryLeagues, sourceKindOf, timeAgo, type LibraryFilter } from '@/shared/source'
import type { Highlight } from '@/shared/types'

export default function ClipsScreen() {
  const insets = useSafeAreaInsets()
  const router = useRouter()
  const { url: base } = useApi()
  const { data, error, loading, refreshing, refresh } = useHighlights()
  const [league, setLeague] = useState<LibraryFilter>('all')
  const leagues = useMemo(() => libraryLeagues(data ?? []), [data])
  const items = useMemo(() => filterLibrary(data ?? [], league), [data, league])

  const play = (h: Highlight) => {
    const video = absUrl(base, highlightVideo(h))
    if (video) {
      setSoundOn(true)   // tapped to watch: the full-screen player plays with sound
      router.push({ pathname: '/player', params: {
        url: video, title: h.title, league: h.league, kind: sourceKindOf(h),
        poster: absUrl(base, highlightPoster(h)) ?? '',
      } })
    } else if (h.youtube_id) {
      Linking.openURL(`https://www.youtube.com/watch?v=${h.youtube_id}`).catch(() => {})
    }
  }

  const header = (
    <View>
      <View style={s.top}>
        <Logo />
        <Text style={s.count}>{data ? `${items.length} clips` : ''}</Text>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.filters}>
        <Chip on={league === 'all'} onPress={() => setLeague('all')}>All</Chip>
        {leagues.map(l => <Chip key={l} on={league === l} onPress={() => setLeague(l)}>{l.toUpperCase()}</Chip>)}
      </ScrollView>
      {error && data && <Text style={s.stale}>Couldn't refresh, showing the last library.</Text>}
    </View>
  )

  let empty = null
  if (!data && loading) empty = <View>{[0, 1, 2].map(i => <Skeleton key={i} height={220} />)}</View>
  else if (!data && error) empty = <StateBox title="Clips unavailable" message={describeError(error, base, 'clip library')} onRetry={refresh} busy={refreshing} />
  else if (data && !items.length) empty = <StateBox title="No clips yet" message={league === 'all' ? 'Clips appear here as big plays are captured.' : `No ${league.toUpperCase()} clips yet.`} />

  return (
    <FlatList
      style={{ backgroundColor: C.bg }}
      contentContainerStyle={{ paddingTop: insets.top + 6, paddingHorizontal: 16, paddingBottom: 24 }}
      data={items}
      keyExtractor={h => h.event_id}
      renderItem={({ item }) => <LibraryCard h={item} base={base} onPress={() => play(item)} />}
      ListHeaderComponent={header}
      ListEmptyComponent={empty}
      initialNumToRender={6}
      windowSize={7}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={C.accent} />}
    />
  )
}

const LibraryCard = memo(function LibraryCard({ h, base, onPress }: { h: Highlight; base: string; onPress: () => void }) {
  const poster = absUrl(base, highlightPoster(h))
  const duration = formatDuration(h.clip_duration)
  const matchup = h.away && h.home ? `${h.away} @ ${h.home}` : ''
  const score = h.away_score != null && h.home_score != null ? ` · ${h.away_score}-${h.home_score}` : ''
  const when = timeAgo(h.occurred_utc ?? h.published_utc ?? h.received_utc)
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={`Play ${h.title}`} testID={`library-${h.event_id}`}
      style={({ pressed }) => [s.card, pressed && { opacity: 0.85 }]}>
      <MediaFrame style={[s.media, !poster && { backgroundColor: C.panel2 }]} testID="library-media">
        {poster ? <Image source={{ uri: poster }} style={fillFrame} contentFit="contain" transition={150} cachePolicy="memory-disk" />
          : <Text style={s.fallback} numberOfLines={3}>{h.title}</Text>}
        <View style={s.playBtn}><Text style={s.playIcon}>▶</Text></View>
        {!!duration && <Text style={s.duration}>{duration}</Text>}
        {!h.file && !!h.youtube_id && <Text style={s.yt}>YouTube</Text>}
      </MediaFrame>
      <View style={s.meta} testID="library-meta">
        <View style={s.metaRow}>
          <Text style={[s.league, { color: h.league === 'nfl' ? C.nfl : h.league === 'mlb' ? C.mlb : C.text2 }]}>{(h.league || '').toUpperCase()}</Text>
          <SourcePill kind={sourceKindOf(h)} league={h.league} />
          <Text style={s.when}>{when}</Text>
        </View>
        <Text style={s.title} numberOfLines={2}>{h.title}</Text>
        {!!(matchup || h.series_description) && <Text style={s.sub} numberOfLines={1}>{[matchup + score, h.series_description].filter(Boolean).join(' · ')}</Text>}
      </View>
    </Pressable>
  )
})

const s = StyleSheet.create({
  top: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', height: 44 },
  count: { color: C.muted, fontSize: 12 },
  filters: { gap: 8, paddingBottom: 12 },
  stale: { color: C.warn, fontSize: 12, marginBottom: 8 },
  card: { width: '100%', maxWidth: MEDIA_MAX_WIDTH, alignSelf: 'center', borderRadius: RADIUS, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, overflow: 'hidden', marginBottom: 12 },
  media: { alignItems: 'center', justifyContent: 'center' },
  fallback: { color: C.text2, padding: 16, textAlign: 'center', fontWeight: '600' },
  playBtn: { width: 52, height: 52, borderRadius: 26, backgroundColor: 'rgba(255,106,26,0.92)', alignItems: 'center', justifyContent: 'center' },
  playIcon: { color: C.white, fontSize: 22, marginLeft: 3 },
  duration: { position: 'absolute', right: 8, bottom: 8, overflow: 'hidden', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: 'rgba(0,0,0,0.7)', color: C.white, fontSize: 11, fontFamily: F.mono },
  yt: { position: 'absolute', left: 8, bottom: 8, overflow: 'hidden', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: '#c4302b', color: C.white, fontSize: 10, fontWeight: '700' },
  meta: { padding: 10, gap: 4 },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  league: { fontWeight: '800', fontSize: 11, letterSpacing: 1 },
  when: { marginLeft: 'auto', color: C.muted, fontSize: 11 },
  title: { color: C.text, fontSize: 15, fontWeight: '700', lineHeight: 20 },
  sub: { color: C.muted, fontSize: 12 },
})
