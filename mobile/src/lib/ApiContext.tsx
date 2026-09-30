import AsyncStorage from '@react-native-async-storage/async-storage'
import Constants from 'expo-constants'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Platform } from 'react-native'

import { normalizeBaseUrl, resolveApiBase, type ApiBase } from '@/shared/apiBase'

const STORAGE_KEY = 'bigplays.apiUrl'

interface ApiCtx extends ApiBase {
  /** What the URL would be without the Settings override. */
  auto: ApiBase
  override: string | null
  /** True once the saved override has been read (avoid a first fetch against the wrong host). */
  ready: boolean
  setOverride: (url: string | null) => Promise<void>
}

const Ctx = createContext<ApiCtx | null>(null)

function metroHostUri(): string | null {
  const c = Constants as unknown as {
    expoConfig?: { hostUri?: string } | null
    expoGoConfig?: { debuggerHost?: string } | null
    manifest2?: { extra?: { expoGo?: { debuggerHost?: string } } } | null
  }
  return c.expoConfig?.hostUri ?? c.expoGoConfig?.debuggerHost ?? c.manifest2?.extra?.expoGo?.debuggerHost ?? null
}

function autoBase(): ApiBase {
  return resolveApiBase({
    envUrl: process.env.EXPO_PUBLIC_API_URL,
    port: process.env.EXPO_PUBLIC_API_PORT,
    hostUri: Platform.OS === 'web' ? null : metroHostUri(),
    webHostname: Platform.OS === 'web' && typeof window !== 'undefined' ? window.location.hostname : null,
  })
}

export function ApiProvider({ children }: { children: ReactNode }) {
  const [override, setOverrideState] = useState<string | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let alive = true
    AsyncStorage.getItem(STORAGE_KEY)
      .then(v => { if (alive) setOverrideState(normalizeBaseUrl(v)) })
      .catch(() => { /* storage unavailable: use the auto URL */ })
      .finally(() => { if (alive) setReady(true) })
    return () => { alive = false }
  }, [])

  const setOverride = useCallback(async (url: string | null) => {
    const v = normalizeBaseUrl(url)
    setOverrideState(v)
    try {
      if (v) await AsyncStorage.setItem(STORAGE_KEY, v)
      else await AsyncStorage.removeItem(STORAGE_KEY)
    } catch { /* not persisted; still applies for this session */ }
  }, [])

  const value = useMemo<ApiCtx>(() => {
    const auto = autoBase()
    const eff = override ? { url: override, source: 'override' as const } : auto
    return { ...eff, auto, override, ready, setOverride }
  }, [override, ready, setOverride])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useApi(): ApiCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('useApi must be used inside <ApiProvider>')
  return v
}
