// Settings tab: API base URL override + connection status.
import Constants from 'expo-constants'
import { useCallback, useEffect, useState } from 'react'
import { ActivityIndicator, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { Logo } from '@/components/ui'
import { useApi } from '@/lib/ApiContext'
import { C, F, RADIUS } from '@/lib/theme'
import { ApiError, describeError, getJson } from '@/shared/api'
import { SOURCE_LABEL, normalizeBaseUrl } from '@/shared/apiBase'

type Status =
  | { state: 'checking' }
  | { state: 'ok'; ms: number; mode?: string; highlights?: number }
  | { state: 'error'; message: string }

async function ping(base: string): Promise<Status> {
  const t0 = Date.now()
  try {
    const body = await getJson<{ mode?: string; highlights?: number }>(base, '/api/status', undefined, 6000)
    return { state: 'ok', ms: Date.now() - t0, mode: body.mode, highlights: body.highlights }
  } catch (e) {
    return { state: 'error', message: e instanceof ApiError && !e.network ? `Reached ${base}, but /api/status failed (${e.message}).` : describeError(e, base) }
  }
}

export default function SettingsScreen() {
  const insets = useSafeAreaInsets()
  const api = useApi()
  const [draft, setDraft] = useState(api.override ?? '')
  const [status, setStatus] = useState<Status>({ state: 'checking' })
  const [draftStatus, setDraftStatus] = useState<Status | null>(null)

  useEffect(() => { setDraft(api.override ?? '') }, [api.override])

  const check = useCallback(async () => {
    setStatus({ state: 'checking' })
    setStatus(await ping(api.url))
  }, [api.url])
  useEffect(() => { if (api.ready) void check() }, [api.ready, check])

  const normalized = normalizeBaseUrl(draft)
  const invalid = draft.trim() !== '' && !normalized

  const test = async () => {
    if (!normalized) return
    setDraftStatus({ state: 'checking' })
    setDraftStatus(await ping(normalized))
  }
  const save = async () => { if (normalized) { await api.setOverride(normalized); setDraftStatus(null) } }
  const reset = async () => { await api.setOverride(null); setDraft(''); setDraftStatus(null) }

  const hostUri = (Constants.expoConfig as { hostUri?: string } | null)?.hostUri ?? '—'

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: C.bg }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={{ paddingTop: insets.top + 6, paddingHorizontal: 16, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
        <View style={s.top}><Logo /></View>

        <Text style={s.h2}>Backend</Text>
        <View style={s.card}>
          <Row label="API URL" value={api.url} mono />
          <Row label="Source" value={SOURCE_LABEL[api.source]} />
          <View style={s.statusRow}>
            <StatusDot status={status} />
            <Text style={s.statusText}>
              {status.state === 'checking' ? 'Checking…'
                : status.state === 'ok' ? `Connected · ${status.ms} ms${status.mode ? ` · ${status.mode} mode` : ''}${status.highlights != null ? ` · ${status.highlights} clips` : ''}`
                : status.message}
            </Text>
          </View>
          <Pressable style={s.btn} onPress={check} accessibilityRole="button"><Text style={s.btnText}>Check again</Text></Pressable>
        </View>

        <Text style={s.h2}>Override URL</Text>
        <View style={s.card}>
          <Text style={s.help}>Leave empty to use the automatic URL ({api.auto.url}, from {SOURCE_LABEL[api.auto.source]}).</Text>
          <TextInput
            value={draft} onChangeText={t => { setDraft(t); setDraftStatus(null) }}
            placeholder="http://100.78.97.55:8765" placeholderTextColor={C.muted}
            autoCapitalize="none" autoCorrect={false} keyboardType="url" returnKeyType="done" onSubmitEditing={test}
            style={[s.input, invalid && { borderColor: C.live }]} accessibilityLabel="API URL override" />
          {invalid && <Text style={[s.help, { color: C.live }]}>Enter a URL like http://192.168.1.20:8000</Text>}
          {draftStatus && (
            <View style={s.statusRow}>
              <StatusDot status={draftStatus} />
              <Text style={s.statusText}>{draftStatus.state === 'checking' ? 'Testing…' : draftStatus.state === 'ok' ? `Reachable · ${draftStatus.ms} ms` : draftStatus.message}</Text>
            </View>
          )}
          <View style={s.btnRow}>
            <Pressable style={[s.btn, !normalized && s.btnDisabled]} disabled={!normalized} onPress={test} accessibilityRole="button"><Text style={s.btnText}>Test</Text></Pressable>
            <Pressable style={[s.btn, s.btnPrimary, !normalized && s.btnDisabled]} disabled={!normalized} onPress={save} accessibilityRole="button"><Text style={[s.btnText, { color: C.white }]}>Save</Text></Pressable>
            <Pressable style={[s.btn, !api.override && s.btnDisabled]} disabled={!api.override} onPress={reset} accessibilityRole="button"><Text style={s.btnText}>Use automatic</Text></Pressable>
          </View>
        </View>

        <Text style={s.h2}>Help</Text>
        <View style={s.card}>
          <Text style={s.help}>The backend must listen on an address this phone can reach. 127.0.0.1 only works on the Mac itself.</Text>
          <Text style={s.help}>• Tailscale: http://&lt;mac-tailscale-ip&gt;:8765 (the tailnet proxy, e.g. http://100.78.97.55:8765).</Text>
          <Text style={s.help}>• Same Wi-Fi: http://&lt;mac-lan-ip&gt;:8000 with the backend started with --host 0.0.0.0.</Text>
          <Row label="Metro host" value={hostUri} mono />
          <Row label="EXPO_PUBLIC_API_URL" value={process.env.EXPO_PUBLIC_API_URL || '(not set)'} mono />
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <View style={s.row}>
      <Text style={s.rowLabel}>{label}</Text>
      <Text style={[s.rowValue, mono && { fontFamily: F.mono, fontSize: 12 }]} selectable>{value}</Text>
    </View>
  )
}

function StatusDot({ status }: { status: Status }) {
  if (status.state === 'checking') return <ActivityIndicator size="small" color={C.text2} />
  return <View style={[s.dot, { backgroundColor: status.state === 'ok' ? C.ok : C.live }]} />
}

const s = StyleSheet.create({
  top: { flexDirection: 'row', alignItems: 'center', height: 44 },
  h2: { color: C.text, fontSize: 20, fontWeight: '800', fontFamily: F.display, marginTop: 14, marginBottom: 8 },
  card: { padding: 12, borderRadius: RADIUS, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, gap: 8 },
  row: { gap: 2 },
  rowLabel: { color: C.muted, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.8 },
  rowValue: { color: C.text, fontSize: 14 },
  statusRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, paddingVertical: 4 },
  statusText: { color: C.text2, fontSize: 13, flex: 1, lineHeight: 18 },
  dot: { width: 10, height: 10, borderRadius: 5, marginTop: 4 },
  help: { color: C.text2, fontSize: 12, lineHeight: 17 },
  input: { borderWidth: 1, borderColor: C.line2, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, color: C.text, backgroundColor: C.bg2, fontFamily: F.mono, fontSize: 14 },
  btnRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  btn: { alignSelf: 'flex-start', paddingVertical: 8, paddingHorizontal: 14, borderRadius: 8, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line2 },
  btnPrimary: { backgroundColor: C.accent, borderColor: C.accent },
  btnDisabled: { opacity: 0.4 },
  btnText: { color: C.text, fontWeight: '600', fontSize: 13 },
})
