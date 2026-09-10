export type League = 'nba' | 'nfl'

export interface LLMJudgment {
  verdict: boolean
  hype_score: number
  tags: string[]
  title: string
  rationale: string
}

export interface Highlight {
  event_id: string
  game_id: string
  league: League
  occurred_utc: string
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
  social?: string[]
  commentary?: string[]
  storage_uri?: string | null
  date?: string
  youtube_id?: string | null
  youtube_start?: number
  youtube_end?: number | null
  source?: { title?: string; channel?: string; url?: string | null }
  demo?: boolean
  ts?: number
}

export interface Game {
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
}

export type Stage = 'ingest' | 'retrieve' | 'heuristic' | 'social' | 'llm' | 'clip' | 'store'
export const STAGES: Stage[] = ['ingest', 'retrieve', 'heuristic', 'social', 'llm', 'clip', 'store']
export const STAGE_LABEL: Record<Stage, string> = {
  ingest: 'Ingest',
  retrieve: 'RAG retrieve',
  heuristic: 'Heuristics',
  social: 'Social signal',
  llm: 'Claude judgment',
  clip: 'FFmpeg clip',
  store: 'S3 + tag',
}

export interface PipelineEvent {
  play_id: string
  stage: Stage
  status: 'running' | 'done'
  detail: string
  data?: Record<string, unknown>
  ts: number
}

export interface LogLine {
  id: number
  ts: number
  level: 'info' | 'warn' | 'error' | 'stage' | 'highlight'
  msg: string
}
