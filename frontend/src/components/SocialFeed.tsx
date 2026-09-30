import { useEffect, useState } from 'react'
import type { SocialFeedResponse, SocialPlayMatch, SocialPost, SocialProviderStatus } from '../types'
import { eventTime, timeAgo } from './util'

type Load<T> = { state: 'loading' } | { state: 'ok'; data: T } | { state: 'unavailable'; reason: string }

const STATUS_LABEL: Record<string, string> = {
  ok: 'Connected',
  ready: 'Ready',
  configured: 'Configured',
  error: 'Error',
  not_checked: 'Not checked yet',
  needs_token: 'Needs token',
  disabled_paid: 'Paid API · disabled',
  missing_credentials: 'Missing credentials',
  disabled: 'Disabled',
}
// Traffic-light class for each provider state.
const STATUS_TONE: Record<string, 'good' | 'warn' | 'bad' | 'off'> = {
  ok: 'good', ready: 'good', configured: 'good', error: 'bad',
  not_checked: 'warn', needs_token: 'warn', missing_credentials: 'warn', disabled_paid: 'off', disabled: 'off',
}

const POLL_MS = 30000

async function getJSON(url: string): Promise<{ ok: true; data: unknown } | { ok: false; reason: string }> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(10000), headers: { accept: 'application/json' } })
    if (response.status === 404) return { ok: false, reason: 'Social endpoint not deployed yet' }
    if (!response.ok) return { ok: false, reason: `Social service returned HTTP ${response.status}` }
    // The SPA fallback answers unknown paths with index.html, so insist on JSON.
    if (!(response.headers.get('content-type') ?? '').includes('json')) return { ok: false, reason: 'Social endpoint not deployed yet' }
    return { ok: true, data: await response.json() }
  } catch {
    return { ok: false, reason: 'Social service unreachable' }
  }
}

/** Accepts {providers: [...]}, a bare array, or a {provider: {...}|"status"} map. */
export function normalizeStatus(raw: unknown): SocialProviderStatus[] {
  const source = raw && typeof raw === 'object' && !Array.isArray(raw) && 'providers' in raw
    ? (raw as { providers: unknown }).providers : raw
  if (Array.isArray(source)) {
    return source.filter(p => p && typeof p === 'object' && 'provider' in p) as SocialProviderStatus[]
  }
  if (source && typeof source === 'object') {
    return Object.entries(source as Record<string, unknown>).map(([provider, value]) => typeof value === 'string'
      ? { provider, status: value }
      : { provider, status: 'not_checked', ...(value as object) } as SocialProviderStatus)
  }
  return []
}

function normalizePosts(raw: unknown): SocialPost[] {
  const list = Array.isArray(raw) ? raw
    : raw && typeof raw === 'object' ? (raw as { posts?: unknown; items?: unknown }).posts ?? (raw as { items?: unknown }).items : null
  if (!Array.isArray(list)) return []
  const seen = new Set<string>()
  return list.filter((p): p is SocialPost => !!p && typeof p === 'object' && typeof p.id === 'string' && typeof p.text === 'string'
    && !seen.has(`${p.provider}:${p.id}`) && !!seen.add(`${p.provider}:${p.id}`))
}

function metricText(metrics: SocialPost['metrics']): string {
  if (!metrics) return ''
  const fmt = (v: number) => Intl.NumberFormat(undefined, { notation: 'compact' }).format(v)
  return (['likes', 'reposts', 'replies'] as const).filter(k => typeof metrics[k] === 'number')
    .map(k => `${fmt(metrics[k] as number)} ${k}`).join(' · ')
}

function ageText(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return ''
  return seconds < 120 ? `${Math.round(seconds)}s old` : `${Math.round(seconds / 60)}m old`
}

type FeedMeta = Pick<SocialFeedResponse, 'stale' | 'cached' | 'cache_age_seconds' | 'fetched_at' | 'play_evidence_count'>

export interface SocialFeedProps {
  league: 'nfl' | 'mlb'
  limit?: number
  /** Clip currently on screen; posts matched to it are highlighted. */
  currentClipId?: string | null
  clipTitle?: (clipId: string) => string | undefined
  onSelectClip?: (clipId: string) => void
}

export function SocialFeed({ league, limit = 30, currentClipId, clipTitle, onSelectClip }: SocialFeedProps) {
  const [status, setStatus] = useState<Load<SocialProviderStatus[]>>({ state: 'loading' })
  const [posts, setPosts] = useState<Load<SocialPost[]>>({ state: 'loading' })
  const [meta, setMeta] = useState<FeedMeta>({})
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    let disposed = false
    setPosts({ state: 'loading' })
    async function refresh() {
      const [s, f] = await Promise.all([getJSON('/api/social/status'), getJSON(`/api/social/feed?league=${league}&limit=${limit}`)])
      if (disposed) return
      const feed = (f.ok && f.data && typeof f.data === 'object' ? f.data : {}) as SocialFeedResponse
      // Prefer /status; the feed response carries the same provider block as a fallback.
      const providers = s.ok ? normalizeStatus(s.data) : f.ok && feed.providers ? normalizeStatus(feed) : null
      setStatus(providers ? { state: 'ok', data: providers } : { state: 'unavailable', reason: s.ok ? '' : s.reason })
      setPosts(f.ok ? { state: 'ok', data: normalizePosts(f.data) } : { state: 'unavailable', reason: f.reason })
      setMeta(f.ok ? { stale: feed.stale, cached: feed.cached, cache_age_seconds: feed.cache_age_seconds,
        fetched_at: feed.fetched_at, play_evidence_count: feed.play_evidence_count } : {})
      setNow(Date.now())
    }
    void refresh()
    const timer = setInterval(refresh, POLL_MS)
    return () => { disposed = true; clearInterval(timer) }
  }, [league, limit])

  const all = posts.state === 'ok' ? posts.data : []
  const evidence = all.filter(p => p.relevance === 'play_evidence')
  const chatter = all.filter(p => p.relevance !== 'play_evidence')
  const context = { now, currentClipId, clipTitle, onSelectClip }

  return (
    <section className="social-feed" aria-label="Social feed">
      <div className="panel-head">
        <h3>Social · {league.toUpperCase()}</h3>
        <span className="count mono">{posts.state === 'ok' ? `${all.length} posts` : ''}</span>
      </div>
      <div className="social-providers" aria-label="Social provider status">
        {status.state === 'loading' && <span className="muted">Checking providers…</span>}
        {status.state === 'unavailable' && <span className="provider-status unavailable">{status.reason}</span>}
        {status.state === 'ok' && !status.data.length && <span className="muted">No social providers configured</span>}
        {status.state === 'ok' && status.data.map(p => (
          <span key={p.provider} className={`provider-status ${STATUS_TONE[p.status] ?? 'warn'}`} data-provider={p.provider}
            data-status={p.status} title={p.note ?? undefined}>
            <i />{p.provider} · {STATUS_LABEL[p.status] ?? p.status.replace(/_/g, ' ')}
            {p.serving_stale_posts && <em> · stale posts</em>}
            {p.used_in_feed === false && <em className="muted"> · not in feed</em>}
          </span>
        ))}
      </div>
      {posts.state === 'ok' && (meta.stale || meta.cached) && <div className={`social-cache ${meta.stale ? 'stale' : ''}`} role="status">
        {meta.stale ? 'Showing stale cached posts' : 'Cached'}{meta.cache_age_seconds != null ? ` · ${ageText(meta.cache_age_seconds)}` : ''}
        {meta.fetched_at && <> · fetched <time dateTime={meta.fetched_at}>{timeAgo(meta.fetched_at, now) || eventTime(meta.fetched_at)}</time></>}
      </div>}
      {posts.state === 'loading' && <div className="social-empty muted"><span className="spinner" /> Loading posts…</div>}
      {posts.state === 'unavailable' && <div className="social-empty muted" role="status">Social feed unavailable: {posts.reason}.</div>}
      {posts.state === 'ok' && <>
        <PostGroup title="Play evidence" hint="Posts matched to a specific play" kind="play_evidence" posts={evidence} {...context} />
        <PostGroup title="General chatter" hint="Game talk, not tied to a play" kind="general_chatter" posts={chatter} {...context} />
      </>}
    </section>
  )
}

function matchText(m: SocialPlayMatch): string {
  const bits = [m.confidence ? `${m.confidence} confidence` : '',
    m.seconds_after_play != null ? `+${Math.round(m.seconds_after_play)}s after play` : '',
    [...(m.players ?? []), ...(m.actions ?? [])].slice(0, 3).join(', ')]
  return bits.filter(Boolean).join(' · ')
}

type GroupProps = { title: string; hint: string; kind: string; posts: SocialPost[]; now: number } & Pick<SocialFeedProps, 'currentClipId' | 'clipTitle' | 'onSelectClip'>

function PostGroup({ title, hint, kind, posts, now, currentClipId, clipTitle, onSelectClip }: GroupProps) {
  return (
    <div className={`social-group ${kind}`} role="group" aria-label={title}>
      <div className="social-group-head">
        <span className={`relevance-pill ${kind}`}>{title.toUpperCase()}</span>
        <span className="muted">{hint}</span>
        <span className="count mono">{posts.length}</span>
      </div>
      {!posts.length && <div className="social-empty muted">No {title.toLowerCase()} yet.</div>}
      <ul className="social-posts">
        {posts.map(p => {
          const matches = p.play_matches ?? []
          const onCurrent = !!currentClipId && matches.some(m => m.clip_id === currentClipId)
          return <li key={`${p.provider}:${p.id}`} className={`social-post ${onCurrent ? 'current' : ''}`} data-post-id={p.id}>
            <div className="social-post-meta">
              <span className="q-src social">{p.provider}</span>
              {p.author_display_name && <b>{p.author_display_name}</b>}
              {p.created_at && <time dateTime={p.created_at} title={`Posted ${eventTime(p.created_at)}`} className="muted">{timeAgo(p.created_at, now) || eventTime(p.created_at)}</time>}
            </div>
            <p>{p.text}</p>
            {kind === 'play_evidence' && matches.map(m => {
              const label = clipTitle?.(m.clip_id)
              return <div key={m.clip_id} className="play-match" data-clip-id={m.clip_id}>
                {label && onSelectClip
                  ? <button onClick={() => onSelectClip(m.clip_id)}>▶ {label}</button>
                  : <span className="mono">clip {m.clip_id}</span>}
                <span className="muted">{matchText(m)}</span>
              </div>
            })}
            <div className="social-post-foot">
              <span className="muted mono">{metricText(p.metrics)}</span>
              {p.url && <a href={p.url} target="_blank" rel="noreferrer">Open post</a>}
            </div>
          </li>
        })}
      </ul>
    </div>
  )
}
