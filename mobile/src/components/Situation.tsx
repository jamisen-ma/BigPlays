// Ported from frontend/src/games/Situation.tsx (SVG diamond -> rotated Views).
import { StyleSheet, Text, View } from 'react-native'

import { C, F } from '@/lib/theme'
import { downDistanceText } from '@/shared/format'
import { isMlbSituation, isNflSituation, type MlbSituation, type NflSituation, type ScoreGame } from '@/shared/types'

export function Diamond({ s, size = 34 }: { s: Pick<MlbSituation, 'on_first' | 'on_second' | 'on_third'>; size?: number }) {
  const b = Math.round(size * 0.3)
  const base = (on: boolean, left: number, top: number) => (
    <View style={[st.base, { width: b, height: b, left, top }, on && st.baseOn]} />
  )
  const runners = [s.on_first && '1st', s.on_second && '2nd', s.on_third && '3rd'].filter(Boolean)
  const h = size * 0.8
  return (
    <View style={{ width: size, height: h }} accessibilityRole="image"
      accessibilityLabel={runners.length ? `Runners on ${runners.join(', ')}` : 'Bases empty'}>
      {base(s.on_second, size / 2 - b / 2, h * 0.28 - b / 2)}
      {base(s.on_third, size * 0.22 - b / 2, h * 0.62 - b / 2)}
      {base(s.on_first, size * 0.78 - b / 2, h * 0.62 - b / 2)}
    </View>
  )
}

export function Outs({ outs }: { outs: number | null }) {
  return (
    <View style={st.outs} accessibilityLabel={outs != null ? `${outs} ${outs === 1 ? 'out' : 'outs'}` : 'Outs unknown'}>
      {[0, 1, 2].map(i => <View key={i} style={[st.out, outs != null && i < outs && st.outOn]} />)}
    </View>
  )
}

export function MlbSituationView({ s, compact }: { s: MlbSituation; compact?: boolean }) {
  const count = s.balls != null && s.strikes != null ? `${s.balls}-${s.strikes}` : '–'
  return (
    <View style={[st.mlb, compact && st.mlbCompact]}>
      <Diamond s={s} size={compact ? 34 : 48} />
      <View style={[st.bso, !compact && { alignItems: 'flex-start' }]}>
        <Text style={st.count} accessibilityLabel="Balls-strikes">{count}</Text>
        <Outs outs={s.outs} />
        {!compact && <Text style={st.outsText}>{s.outs != null ? `${s.outs} out` : ''}</Text>}
      </View>
      {!compact && (s.batter || s.pitcher) && (
        <View style={st.matchup}>
          {!!s.batter && <Text style={st.matchupText} numberOfLines={1}><Text style={st.muted}>AB </Text>{s.batter}</Text>}
          {!!s.pitcher && <Text style={st.matchupText} numberOfLines={1}><Text style={st.muted}>P </Text>{s.pitcher}</Text>}
        </View>
      )}
    </View>
  )
}

export function NflSituationView({ s, compact }: { s: NflSituation; compact?: boolean }) {
  const text = downDistanceText(s)
  if (!text) return null
  return (
    <View style={[st.nfl, !compact && { alignItems: 'center', maxWidth: undefined }]}>
      <Text style={[st.dd, !compact && { textAlign: 'center', fontSize: 14 }]}>{text}</Text>
      {s.is_red_zone && <Text style={st.rz}>RED ZONE</Text>}
    </View>
  )
}

export function SituationView({ game, compact }: { game: ScoreGame; compact?: boolean }) {
  if (game.status !== 'in' || !game.situation) return null
  if (game.league === 'mlb' && isMlbSituation(game.situation)) return <MlbSituationView s={game.situation} compact={compact} />
  if (game.league === 'nfl' && isNflSituation(game.situation)) return <NflSituationView s={game.situation} compact={compact} />
  return null
}

const st = StyleSheet.create({
  base: { position: 'absolute', transform: [{ rotate: '45deg' }], borderWidth: 1.5, borderColor: C.muted, backgroundColor: 'transparent' },
  baseOn: { backgroundColor: C.warn, borderColor: C.warn },
  outs: { flexDirection: 'row', gap: 3 },
  out: { width: 7, height: 7, borderRadius: 4, borderWidth: 1, borderColor: C.muted },
  outOn: { backgroundColor: C.live, borderColor: C.live },
  mlb: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  mlbCompact: { flexDirection: 'column', gap: 2 },
  bso: { alignItems: 'center', gap: 3 },
  count: { color: C.text, fontSize: 13, fontWeight: '600', fontFamily: F.mono },
  outsText: { fontSize: 10, color: C.muted },
  matchup: { gap: 2, flexShrink: 1 },
  matchupText: { color: C.text, fontSize: 12 },
  muted: { color: C.muted },
  nfl: { alignItems: 'flex-end', gap: 4, maxWidth: 130 },
  dd: { color: C.text, fontSize: 12, fontWeight: '600', textAlign: 'right' },
  rz: { overflow: 'hidden', fontWeight: '800', fontSize: 10, letterSpacing: 1, paddingHorizontal: 6, paddingVertical: 1, borderRadius: 4, backgroundColor: C.live, color: C.white },
})
