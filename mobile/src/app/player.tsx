// Full-screen clip player (modal), opened from the Clips tab.
import { useLocalSearchParams, useRouter } from 'expo-router'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { SourcePill } from '@/components/ClipCard'
import { VideoPlayer } from '@/components/VideoPlayer'
import { C } from '@/lib/theme'
import { SOURCE_KINDS, type SourceKind } from '@/shared/types'

export default function PlayerScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const p = useLocalSearchParams<{ url?: string; title?: string; league?: string; kind?: string }>()
  const kind = SOURCE_KINDS.includes(p.kind as SourceKind) ? (p.kind as SourceKind) : null
  const close = () => (router.canGoBack() ? router.back() : router.replace('/clips'))

  return (
    <View style={s.screen}>
      <View style={s.stage}>
        {p.url ? <VideoPlayer key={p.url} url={p.url} fill /> : <Text style={s.missing}>No video URL</Text>}
      </View>
      <View style={[s.top, { paddingTop: insets.top + 8 }]} pointerEvents="box-none">
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
    </View>
  )
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#000' },
  stage: { flex: 1, justifyContent: 'center' },
  missing: { color: C.text2, textAlign: 'center' },
  top: { position: 'absolute', top: 0, left: 0, right: 0, paddingHorizontal: 16, flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  info: { flex: 1, gap: 4, paddingTop: 2 },
  close: { width: 36, height: 36, borderRadius: 18, backgroundColor: 'rgba(0,0,0,0.6)', alignItems: 'center', justifyContent: 'center' },
  closeText: { color: C.white, fontSize: 18 },
  league: { color: C.text2, fontWeight: '800', fontSize: 11, letterSpacing: 1 },
  title: { color: C.white, fontSize: 16, fontWeight: '700', textShadowColor: '#000', textShadowRadius: 6 },
})
