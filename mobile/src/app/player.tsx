// Full-screen clip player (modal), opened from the Clips tab.
// The title bar sits above the stage (never over the video); the stage holds the largest 16:9
// MediaFrame that fits, and the video letterboxes inside it (contain), so nothing is cropped.
import { useLocalSearchParams, useRouter } from 'expo-router'
import { useState } from 'react'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { SourcePill } from '@/components/ClipCard'
import { MEDIA_ASPECT, MediaFrame } from '@/components/MediaFrame'
import { VideoPlayer } from '@/components/VideoPlayer'
import { C } from '@/lib/theme'
import { SOURCE_KINDS, type SourceKind } from '@/shared/types'

export default function PlayerScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const p = useLocalSearchParams<{ url?: string; title?: string; league?: string; kind?: string }>()
  const kind = SOURCE_KINDS.includes(p.kind as SourceKind) ? (p.kind as SourceKind) : null
  const close = () => (router.canGoBack() ? router.back() : router.replace('/clips'))
  const [stage, setStage] = useState<{ width: number; height: number } | null>(null)
  // Largest 16:9 box that fits the stage in both directions.
  const frameWidth = stage ? Math.floor(Math.min(stage.width, stage.height * MEDIA_ASPECT)) : 0

  return (
    <View style={s.screen}>
      <View style={[s.top, { paddingTop: insets.top + 8 }]}>
        <Pressable onPress={close} accessibilityRole="button" accessibilityLabel="Close" style={s.close} hitSlop={10}>
          <Text style={s.closeText}>✕</Text>
        </Pressable>
        <View style={s.info} pointerEvents="none">
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            {!!p.league && <Text style={s.league}>{p.league.toUpperCase()}</Text>}
            {kind && <SourcePill kind={kind} league={p.league} />}
          </View>
          {!!p.title && <Text style={s.title} numberOfLines={2}>{p.title}</Text>}
        </View>
      </View>
      <View style={[s.stage, { paddingBottom: insets.bottom }]}
        onLayout={e => { const { width, height } = e.nativeEvent.layout; setStage({ width, height: height - insets.bottom }) }}>
        {!p.url ? <Text style={s.missing}>No video URL</Text>
          : frameWidth > 0 && (
            <MediaFrame style={{ width: frameWidth }} testID="player-media">
              <VideoPlayer key={p.url} url={p.url} />
            </MediaFrame>
          )}
      </View>
    </View>
  )
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#000' },
  stage: { flex: 1, alignItems: 'center', justifyContent: 'center', minHeight: 0 },
  missing: { color: C.text2, textAlign: 'center' },
  top: { paddingHorizontal: 16, paddingBottom: 12, flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  info: { flex: 1, gap: 4, paddingTop: 2 },
  close: { width: 36, height: 36, borderRadius: 18, backgroundColor: 'rgba(255,255,255,0.12)', alignItems: 'center', justifyContent: 'center' },
  closeText: { color: C.white, fontSize: 18 },
  league: { color: C.text2, fontWeight: '800', fontSize: 11, letterSpacing: 1 },
  title: { color: C.white, fontSize: 16, fontWeight: '700' },
})
