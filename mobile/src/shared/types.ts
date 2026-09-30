// Ported from frontend/src/games/types.ts + frontend/src/types.ts (docs/games-api-contract.md v1 + v1.1).
// Pure TS: no React / React Native imports, so it's unit-testable and shareable.

/** Where a clip's video came from. See sourceKindOf() for the fallback when the backend omits it. */
export type SourceKind = 'live_capture' | 'replay' | 'official_upload'
export const SOURCE_KINDS: SourceKind[] = ['live_capture', 'replay', 'official_upload']

export type GamesLeague = 'nfl' | 'mlb'
export const GAMES_LEAGUES: GamesLeague[] = ['nfl', 'mlb']
export type GameStatus = 'pre' | 'in' | 'post'

export interface Team {
  id: string
  abbr: string
  name: string
  short_name: string
  logo: string | null
  /** ESPN hex, with or without a leading '#'. */
  color: string | null
  alt_color: string | null
  score: number | null
  record: string | null
  winner: boolean | null
}

export interface MlbSituation {
  balls: number | null
  strikes: number | null
  outs: number | null
  on_first: boolean
  on_second: boolean
  on_third: boolean
  batter: string | null
  pitcher: string | null
}

export interface NflSituation {
  down: number | null
  distance: number | null
  yard_line_text: string | null
  possession: string | null
  is_red_zone: boolean
  down_distance_text: string | null
}

export interface ScoreGame {
  game_id: string
  league: GamesLeague
  status: GameStatus
  status_detail: string | null
  start_utc: string | null
  venue: string | null
  broadcast: string | null
  period: number | null
  period_label: string | null
  clock: string | null
  away: Team
  home: Team
  situation: MlbSituation | NflSituation | null
  last_play_text: string | null
  clip_count: number
  viral_count: number
}

export interface GamesResponse {
  ok: boolean
  league: GamesLeague
  date: string
  updated_utc: string | null
  games: ScoreGame[]
}

export interface LinescorePeriod { label: string; away: number | null; home: number | null }
export type MlbTotals = { R: number | null; H: number | null; E: number | null }
export type NflTotals = { score: number | null }
export interface Linescore {
  periods: LinescorePeriod[]
  totals: { away: MlbTotals | NflTotals; home: MlbTotals | NflTotals } | null
}

export interface ClipRef {
  event_id: string
  title: string
  /** Relative (/clips/<file>) or absolute; null when the file is missing. Resolve with absUrl(). */
  video_url: string | null
  /** Relative, absolute, or a YouTube thumbnail. */
  poster_url: string | null
  duration_seconds: number | null
  source_kind: SourceKind | null
  social_score: number | null
  occurred_utc: string | null
  published_utc: string | null
}

export interface PlayMlb {
  batter: string | null
  pitcher: string | null
  balls: number | null
  strikes: number | null
  outs: number | null
  pitch_count: number | null
  pitcher_pitch_count?: number | null
}

export interface PlayNfl {
  down_distance_text: string | null
  yards: number | null
  drive_id: string | null
  end_down_distance_text: string | null
}

export interface Play {
  play_id: string
  sequence: number
  period: number | null
  period_label: string | null
  clock: string | null
  text: string
  type: string | null
  scoring: boolean
  team_abbr: string | null
  away_score: number | null
  home_score: number | null
  wallclock_utc: string | null
  is_key_play: boolean
  mlb: PlayMlb | null
  nfl?: PlayNfl | null
  related_play_ids?: string[] | null
  clip: ClipRef | null
  alternate_clips?: ClipRef[] | null
  viral: boolean
  viral_reason: string | null
}

export interface GameDetailResponse {
  ok: boolean
  game: ScoreGame
  linescore: Linescore | null
  updated_utc: string | null
  plays: Play[]
  clips_unmatched: ClipRef[]
}

/** Subset of the /api/highlights record that the clip library uses. */
export interface Highlight {
  event_id: string
  game_id: string
  league: string
  title: string
  description?: string
  player?: string
  team?: string
  away?: string
  home?: string
  away_score?: number | null
  home_score?: number | null
  occurred_utc: string | null
  received_utc?: string
  published_utc?: string | null
  date?: string
  series_description?: string
  source_kind?: SourceKind | null
  demo?: boolean
  imported?: boolean
  file: string | null
  poster?: string | null
  youtube_id?: string | null
  clip_duration?: number | null
  combined_score?: number
  social_score?: number | null
  llm?: { hype_score?: number; title?: string } | null
  tags?: string[]
}

export const isMlbSituation = (s: ScoreGame['situation']): s is MlbSituation =>
  !!s && ('on_first' in s || 'balls' in s)
export const isNflSituation = (s: ScoreGame['situation']): s is NflSituation =>
  !!s && ('down' in s || 'down_distance_text' in s)
