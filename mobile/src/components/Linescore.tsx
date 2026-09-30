// Ported from frontend/src/games/Linescore.tsx (table -> horizontal ScrollView of columns).
import { ScrollView, StyleSheet, Text, View } from 'react-native'

import { C, F, RADIUS } from '@/lib/theme'
import { linescoreColumns } from '@/shared/linescore'
import type { Linescore as LinescoreT, ScoreGame } from '@/shared/types'

export function Linescore({ game, linescore }: { game: ScoreGame; linescore: LinescoreT | null }) {
  const cols = linescoreColumns(game, linescore)
  if (!cols) return null
  return (
    <View style={s.wrap} accessibilityLabel="Linescore">
      <View style={s.teamCol}>
        <Text style={[s.cell, s.head]}> </Text>
        <Text style={[s.cell, s.teamCell]}>{game.away.abbr}</Text>
        <Text style={[s.cell, s.teamCell]}>{game.home.abbr}</Text>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ flexGrow: 1 }}>
        {cols.periods.map((p, i) => (
          <View key={i} style={s.col}>
            <Text style={[s.cell, s.head]}>{p.label}</Text>
            <Text style={s.cell}>{p.away}</Text>
            <Text style={s.cell}>{p.home}</Text>
          </View>
        ))}
      </ScrollView>
      <View style={s.totals}>
        {cols.totals.map(t => (
          <View key={t.label} style={s.col}>
            <Text style={[s.cell, s.head]}>{t.label}</Text>
            <Text style={[s.cell, s.tot]}>{t.away}</Text>
            <Text style={[s.cell, s.tot]}>{t.home}</Text>
          </View>
        ))}
      </View>
    </View>
  )
}

const s = StyleSheet.create({
  wrap: { flexDirection: 'row', marginTop: 10, borderRadius: RADIUS, borderWidth: 1, borderColor: C.line, backgroundColor: C.panel, paddingVertical: 6, paddingHorizontal: 6 },
  teamCol: { paddingRight: 4 },
  col: { minWidth: 26, alignItems: 'center' },
  totals: { flexDirection: 'row', borderLeftWidth: 1, borderLeftColor: C.line, paddingLeft: 4, marginLeft: 2 },
  cell: { height: 24, lineHeight: 24, color: C.text2, fontFamily: F.mono, fontSize: 13, textAlign: 'center', paddingHorizontal: 3 },
  head: { color: C.muted, fontSize: 11 },
  teamCell: { color: C.text, fontWeight: '700', textAlign: 'left', fontFamily: F.display, fontSize: 14 },
  tot: { color: C.white, fontWeight: '700' },
})
