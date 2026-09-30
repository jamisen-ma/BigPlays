// Tokens from frontend/src/styles.css (:root).
import { Platform } from 'react-native'

export const C = {
  bg: '#0a0c11',
  bg2: '#0f1218',
  panel: '#12161f',
  panel2: '#171c27',
  line: '#1e2431',
  line2: '#2a3242',
  text: '#e8ebf1',
  text2: '#a6adbd',
  muted: '#6b7382',
  accent: '#ff6a1a',
  accentBg: 'rgba(255,106,26,0.12)',
  accentBorder: 'rgba(255,106,26,0.45)',
  live: '#ff3b47',
  liveBg: 'rgba(255,59,71,0.18)',
  liveBorder: 'rgba(255,59,71,0.55)',
  ok: '#34d399',
  warn: '#fbbf24',
  nfl: '#22c55e',
  mlb: '#60a5fa',
  white: '#ffffff',
} as const

export const SOURCE_COLORS = {
  live_capture: { fg: '#ff8a8f', bg: 'rgba(255,59,71,0.14)', border: 'rgba(255,59,71,0.5)' },
  official_upload: { fg: '#7fb8ff', bg: 'rgba(96,165,250,0.14)', border: 'rgba(96,165,250,0.5)' },
  replay: { fg: '#c4b5fd', bg: 'rgba(167,139,250,0.14)', border: 'rgba(167,139,250,0.5)' },
} as const

export const F = {
  /** Condensed display face stand-in (Barlow Condensed on the web). */
  display: Platform.select({ ios: 'AvenirNextCondensed-Bold', default: undefined }),
  mono: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'ui-monospace, Menlo, monospace' }),
}

export const RADIUS = 12
