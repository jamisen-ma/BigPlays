export type League = 'nba' | 'nfl' | 'ncaaf' | 'mlb'

export interface BaseballState {
  inning?: number | null
  inning_half?: string | null
  balls?: number | null
  strikes?: number | null
  outs?: number | null
  count_context?: string
}

export interface LLMJudgment {
  verdict: boolean
  hype_score: number
  tags: string[]
  title: string
  rationale: string
}

/** Where a clip's video came from. Backend should emit this; see sourceKindOf() for the fallback. */
export type SourceKind = 'live_capture' | 'replay' | 'official_upload'
export const SOURCE_KINDS: SourceKind[] = ['live_capture', 'replay', 'official_upload']

export interface Highlight extends BaseballState {
  event_id: string
  source_kind?: SourceKind | null
  game_id: string
  league: League | 'unknown'
  occurred_utc: string | null
  received_utc?: string
  published_utc?: string | null
  imported?: boolean
  timestamp_status?: 'matched' | 'unresolved' | string
  timestamp_source?: string | null
  season?: number | null
  week?: number | null
  source_play_id?: string | null
  replay_dataset?: string
  media_kind?: 'broadcast'
  video_start?: number
  video_end?: number | null
  clip_duration?: number | null
  reasons: string[]
  base_score: number
  combined_score: number
  tags: string[]
  title: string
  file: string | null
  poster?: string | null
  description?: string
  player?: string
  team?: string
  away?: string
  home?: string
  away_color?: string
  home_color?: string
  away_score?: number
  home_score?: number
  period?: string
  clock?: string
  kind?: string
  llm?: LLMJudgment | null
  social_score?: number
  social_enrichment?: SocialEnrichment | null
  social_assessment?: {
    status: string; reaction?: string; hype_score?: number; confidence?: number; rationale?: string;
    fan_backed?: boolean; thread_url?: string; sources?: { id: string; url: string }[];
  }
  social?: string[]
  commentary?: string[]
  storage_uri?: string | null
  date?: string
  youtube_id?: string | null
  youtube_start?: number
  youtube_end?: number | null
  source?: { title?: string; channel?: string; url?: string | null; play_by_play_url?: string | null }
  ts?: number
}

export interface Game extends BaseballState {
  season?: number | null
  week?: number | null
  game_id: string
  league: League
  away: string
  home: string
  away_color: string
  home_color: string
  away_score: number
  home_score: number
  period: string
  clock: string
  status: string
  status_text?: string
  starts_at?: string
  game_type?: string
  series_description?: string
}

export interface LogLine {
  id: number
  ts: number
  level: 'info' | 'warn' | 'error' | 'highlight'
  msg: string
}

export type SocialRelevance = 'general_chatter' | 'play_evidence'

export interface SocialPlayMatch {
  clip_id: string
  players?: string[]
  teams?: string[]
  actions?: string[]
  seconds_after_play?: number | null
  confidence?: 'high' | 'medium' | string
  method?: string
}

export interface SocialPost {
  id: string
  provider: string
  text: string
  created_at?: string | null
  collected_at?: string | null
  url?: string | null
  author_display_name?: string | null
  author_key?: string | null
  metrics?: { likes?: number | null; reposts?: number | null; replies?: number | null } | null
  relevance?: SocialRelevance | string | null
  play_matches?: SocialPlayMatch[] | null
}

/**
 * mastodon: ok | error | not_checked (+ serving_stale_posts)
 * x: needs_token | disabled_paid | ready
 * reddit: missing_credentials | disabled | configured
 */
export type SocialProviderState = 'ok' | 'error' | 'not_checked' | 'needs_token' | 'disabled_paid' | 'ready'
  | 'missing_credentials' | 'disabled' | 'configured' | string

export interface SocialProviderStatus {
  provider: string
  status: SocialProviderState
  note?: string | null
  used_in_feed?: boolean
  serving_stale_posts?: boolean
}

export interface SocialFeedResponse {
  ok?: boolean
  league?: string
  fetched_at?: string | null
  cached?: boolean
  stale?: boolean
  cache_age_seconds?: number | null
  cache_ttl_seconds?: number | null
  count?: number
  play_evidence_count?: number
  coverage?: unknown
  posts?: SocialPost[]
  providers?: Record<string, Omit<SocialProviderStatus, 'provider'>>
}

export interface SocialEnrichment {
  status: 'matched' | 'no_matches' | string
  post_count?: number
  sources?: unknown[]
}
