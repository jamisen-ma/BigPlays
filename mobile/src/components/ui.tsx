// Small shared primitives: team logo, status pill, segmented control, states.
import { Image } from 'expo-image'
import { useState, type ReactNode } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native'

import { C, F, RADIUS } from '@/lib/theme'
import { statusText, teamColor } from '@/shared/format'
import type { ScoreGame, Team } from '@/shared/types'

export function TeamLogo({ team, size = 28 }: { team: Team; size?: number }) {
  const [failed, setFailed] = useState(false)
  if (!team.logo || failed) {
    return (
      <View style={[s.logoFallback, { width: size, height: size, borderRadius: size / 2, backgroundColor: teamColor(team) }]}>
        <Text style={[s.logoFallbackText, { fontSize: Math.max(8, size * 0.32) }]}>{team.abbr?.slice(0, 3)}</Text>
      </View>
    )
  }
  return <Image source={{ uri: team.logo }} style={{ width: size, height: size }} contentFit="contain"
    cachePolicy="memory-disk" transition={120} onError={() => setFailed(true)} accessibilityIgnoresInvertColors />
}

export function StatusPill({ game }: { game: ScoreGame }) {
  const text = statusText(game)
  if (game.status === 'in') {
    return (
      <View style={s.live} accessibilityLabel={`Live, ${text}`}>
        <View style={s.liveDot} />
        <Text style={s.liveText}>{text}</Text>
      </View>
    )
  }
  return <Text style={[s.status, game.status === 'pre' && { color: C.text }]}>{text}</Text>
}

export function ClipBadge({ count }: { count: number }) {
  if (count <= 0) return null
  return <Text style={s.clipBadge}>🎬 {count} {count === 1 ? 'clip' : 'clips'}</Text>
}

export function Segmented<T extends string>({ options, value, onChange, label }: {
  options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; label?: string
}) {
  return (
    <View style={s.seg} accessibilityRole="tablist" accessibilityLabel={label}>
      {options.map(o => {
        const on = o.value === value
        return (
          <Pressable key={o.value} onPress={() => onChange(o.value)} accessibilityRole="tab" accessibilityState={{ selected: on }}
            style={[s.segBtn, on && s.segBtnOn]} hitSlop={4}>
            <Text style={[s.segText, on && s.segTextOn]}>{o.label}</Text>
          </Pressable>
        )
      })}
    </View>
  )
}

export function Chip({ on, onPress, children, style }: { on?: boolean; onPress: () => void; children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: !!on }}
      style={[s.chip, on && s.chipOn, style]} hitSlop={4}>
      <Text style={[s.chipText, on && s.chipTextOn]}>{children}</Text>
    </Pressable>
  )
}

export function StateBox({ title, message, onRetry, busy }: { title: string; message?: string; onRetry?: () => void; busy?: boolean }) {
  return (
    <View style={s.stateBox} accessibilityRole="alert">
      <Text style={s.stateTitle}>{title}</Text>
      {!!message && <Text style={s.stateMsg}>{message}</Text>}
      {onRetry && (
        <Pressable style={s.retry} onPress={onRetry} accessibilityRole="button">
          {busy ? <ActivityIndicator color={C.text} size="small" /> : <Text style={s.retryText}>Try again</Text>}
        </Pressable>
      )}
    </View>
  )
}

export function Skeleton({ height, style }: { height: number; style?: StyleProp<ViewStyle> }) {
  return <View style={[{ height, borderRadius: RADIUS, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, marginBottom: 10 }, style]} />
}

export function Logo({ size = 24 }: { size?: number }) {
  return <Text style={[s.logo, { fontSize: size }]}>BIG<Text style={{ color: C.accent }}>PLAYS</Text></Text>
}

const s = StyleSheet.create({
  logoFallback: { alignItems: 'center', justifyContent: 'center' },
  logoFallbackText: { color: C.white, fontWeight: '800', fontFamily: F.display },
  live: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: C.liveBg, borderWidth: 1, borderColor: C.liveBorder },
  liveDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: C.live },
  liveText: { color: C.white, fontWeight: '800', fontSize: 12, letterSpacing: 0.8, textTransform: 'uppercase', fontFamily: F.display },
  status: { color: C.text2, fontWeight: '800', fontSize: 12, letterSpacing: 0.8, textTransform: 'uppercase', fontFamily: F.display },
  clipBadge: { overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, fontWeight: '700', fontSize: 11, color: C.accent, backgroundColor: C.accentBg, borderWidth: 1, borderColor: C.accentBorder },
  seg: { flexDirection: 'row', backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 8, padding: 2 },
  segBtn: { paddingVertical: 7, paddingHorizontal: 18, borderRadius: 6 },
  segBtnOn: { backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line2, paddingVertical: 6, paddingHorizontal: 17 },
  segText: { color: C.text2, fontWeight: '700', fontSize: 13, letterSpacing: 0.5 },
  segTextOn: { color: C.white },
  chip: { paddingVertical: 6, paddingHorizontal: 12, borderRadius: 999, borderWidth: 1, borderColor: C.line2, backgroundColor: C.panel },
  chipOn: { borderColor: C.accentBorder, backgroundColor: C.accentBg },
  chipText: { color: C.text2, fontSize: 12, fontWeight: '600' },
  chipTextOn: { color: C.accent },
  stateBox: { marginVertical: 32, marginHorizontal: 4, alignItems: 'center', paddingVertical: 28, paddingHorizontal: 20, borderWidth: 1, borderStyle: 'dashed', borderColor: C.line2, borderRadius: RADIUS, backgroundColor: C.panel },
  stateTitle: { color: C.text, fontSize: 24, fontWeight: '800', fontFamily: F.display, marginBottom: 6, textAlign: 'center' },
  stateMsg: { color: C.muted, fontSize: 13, lineHeight: 19, textAlign: 'center' },
  retry: { marginTop: 14, paddingVertical: 8, paddingHorizontal: 18, borderRadius: 8, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line2, minWidth: 110, alignItems: 'center' },
  retryText: { color: C.text, fontWeight: '600' },
  logo: { color: C.text, fontWeight: '800', letterSpacing: 0.5, fontFamily: F.display },
})
