// API base URL resolution. Pure TS (the platform bits are passed in), so it's unit-testable.

export const API_PORT = 8000
/** Port of scripts/tailnet_proxy.py (100.x.y.z:8765 -> 127.0.0.1:8000) used when Metro is on a Tailscale host. */
export const TAILNET_API_PORT = 8765
export const DEFAULT_API_BASE = `http://127.0.0.1:${API_PORT}`

/** True for Tailscale hosts: CGNAT 100.64.0.0/10 IPv4, the fd7a:115c:a1e0::/48 IPv6 range, or *.ts.net MagicDNS. */
export function isTailnetHost(host: string | null | undefined): boolean {
  const h = (host ?? '').trim().toLowerCase().replace(/^\[|\]$/g, '')
  if (!h) return false
  if (/\.ts\.net$/.test(h)) return true
  if (/^fd7a:115c:a1e0:/.test(h)) return true
  const m = /^100\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/.exec(h)
  return !!m && Number(m[1]) >= 64 && Number(m[1]) <= 127
}

/** Parse EXPO_PUBLIC_API_PORT-style input; null when empty/invalid. */
export function parsePort(raw: string | number | null | undefined): number | null {
  if (raw == null || raw === '') return null
  const n = typeof raw === 'number' ? raw : Number(String(raw).trim())
  return Number.isInteger(n) && n > 0 && n < 65536 ? n : null
}

export type ApiBaseSource = 'override' | 'env' | 'metro' | 'default'
export interface ApiBase { url: string; source: ApiBaseSource }

/**
 * Normalize user/env input into "scheme://host[:port][/path]" with no trailing slash.
 * Adds http:// when the scheme is missing. Returns null for empty or unparseable input.
 */
export function normalizeBaseUrl(raw: string | null | undefined): string | null {
  let s = (raw ?? '').trim()
  if (!s) return null
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = `http://${s}`
  if (!/^https?:\/\/[^/\s?#]+/i.test(s)) return null
  s = s.replace(/[?#].*$/, '').replace(/\/+$/, '')
  // accept a pasted ".../api" and strip it: paths below are always /api/...
  s = s.replace(/\/api$/i, '')
  return s
}

/**
 * Host part of Expo's `hostUri` ("192.168.1.20:8081", "100.101.102.103:8081", "[fe80::1]:8081",
 * "mac.tailnet.ts.net:8081"). Returns null for tunnels (*.exp.direct / ngrok), whose host
 * only forwards Metro, not the BigPlays API.
 */
export function hostFromHostUri(hostUri: string | null | undefined): string | null {
  const s = (hostUri ?? '').trim().replace(/^[a-z]+:\/\//i, '').split('/')[0]
  if (!s) return null
  let host: string
  if (s.startsWith('[')) {
    const end = s.indexOf(']')
    if (end < 0) return null
    host = s.slice(0, end + 1)
  } else {
    host = s.split(':')[0]
  }
  if (!host) return null
  if (/\.exp\.direct$|\.ngrok(-free)?\.(io|app|dev)$|\.expo\.dev$/i.test(host)) return null
  return host
}

export interface ApiBaseInputs {
  /** Saved from the Settings screen (AsyncStorage). */
  override?: string | null
  /** process.env.EXPO_PUBLIC_API_URL */
  envUrl?: string | null
  /** Constants.expoConfig?.hostUri (the Metro dev server the phone loaded from). */
  hostUri?: string | null
  /** On web: window.location.hostname. */
  webHostname?: string | null
  /** Explicit port for the Metro/web-host fallback (process.env.EXPO_PUBLIC_API_PORT). */
  port?: number | string | null
}

/**
 * Settings override > EXPO_PUBLIC_API_URL > Metro host > web page host > 127.0.0.1:8000.
 * Port for the host fallbacks: EXPO_PUBLIC_API_PORT if set, else 8765 for Tailscale hosts
 * (scripts/tailnet_proxy.py), else 8000.
 */
export function resolveApiBase(i: ApiBaseInputs): ApiBase {
  const override = normalizeBaseUrl(i.override)
  if (override) return { url: override, source: 'override' }
  const env = normalizeBaseUrl(i.envUrl)
  if (env) return { url: env, source: 'env' }
  const host = hostFromHostUri(i.hostUri) ?? (i.webHostname?.trim() || null)
  if (host) {
    const port = parsePort(i.port) ?? (isTailnetHost(host) ? TAILNET_API_PORT : API_PORT)
    return { url: `http://${host}:${port}`, source: 'metro' }
  }
  return { url: DEFAULT_API_BASE, source: 'default' }
}

/** Resolve a backend-relative URL (/clips/x.mp4) against the API base. Absolute URLs pass through. */
export function absUrl(base: string, path: string | null | undefined): string | null {
  if (!path) return null
  if (/^[a-z][a-z0-9+.-]*:/i.test(path)) return path
  if (path.startsWith('//')) return `https:${path}`
  return `${base.replace(/\/+$/, '')}${path.startsWith('/') ? '' : '/'}${path}`
}

export const SOURCE_LABEL: Record<ApiBaseSource, string> = {
  override: 'Settings override',
  env: 'EXPO_PUBLIC_API_URL',
  metro: 'Metro dev host',
  default: 'Default (this device)',
}
