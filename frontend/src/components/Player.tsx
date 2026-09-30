import { useEffect, useRef, useState } from 'react'
import type { Highlight } from '../types'
import { HypeBar, HypeMeter } from './HypeMeter'
import { REASON_LABEL, SOURCE_DESCRIPTION, eventTime, gamePhase, hypeOf, sourceKindOf, sourceLabel } from './util'
import { YouTubePlayer } from './YouTubePlayer'

export function Player({ h }: { h: Highlight | null }) {
  const ref = useRef<HTMLVideoElement>(null)
  const [muted, setMuted] = useState(true)
  // footage that failed to load falls back to the locally rendered clip
  const [useLocal, setUseLocal] = useState(false)
  useEffect(() => { setUseLocal(false) }, [h?.event_id])

  useEffect(() => {
    const v = ref.current
    if (!v) return
    v.load()
    v.play().catch(() => {})
  }, [h?.file, useLocal])

  if (!h) {
    return (
      <section className="player empty">
        <div className="player-empty">
          <div className="spinner big" />
          <h2>Waiting for the first viral play…</h2>
          <p>The agent is monitoring live scoreboards, commentary and social signals. Clips appear here the moment one crosses the hype threshold.</p>
        </div>
      </section>
    )
  }

  const src = h.file ? `/clips/${h.file}` : undefined
  const poster = h.poster ? `/clips/${h.poster}` : undefined
  const teamColor = h.team === h.home ? h.home_color : h.away_color
  const showFootage = !!h.youtube_id && !useLocal
  const saved = h.demo || h.imported
  const sourceName = h.source?.channel || h.league.toUpperCase()
  const kind = sourceKindOf(h)

  return (
    <section className="player">
      <div className="video-wrap" style={{ ['--team' as string]: teamColor ?? 'var(--accent)' }}>
        {showFootage ? (
          <YouTubePlayer
            key={`ft-${h.event_id}`}
            videoId={h.youtube_id!}
            start={h.youtube_start ?? 0}
            end={h.youtube_end ?? null}
            onError={() => setUseLocal(true)}
          />
        ) : src ? (
          <video ref={ref} key={h.event_id} src={src} poster={poster} autoPlay loop muted={muted} playsInline controls />
        ) : (
          <div className="video-missing">clip file missing</div>
        )}
        <div className="video-badges">
          <span className={`league-pill ${h.league}`}>{h.league.toUpperCase()}</span>
          <span className={`source-pill ${kind}`} data-source-kind={kind} title={SOURCE_DESCRIPTION[kind]}>{sourceLabel(kind, h.league)}</span>
          <span className="live-pill"><i /> {saved ? showFootage || h.media_kind === 'broadcast' ? 'REPLAY' : 'REPLAY · ANIMATION' : 'AUTO-CLIPPED'}</span>
          <span className="cut-pill mono">{h.clip_duration != null ? `${h.clip_duration}s cut` : h.youtube_end != null ? `${h.youtube_end - (h.youtube_start ?? 0)}s cut` : '−8s / +6s'}</span>
        </div>
        {!showFootage && (
          <div className="video-tools">
            <button className="mute" onClick={() => setMuted(m => !m)} title={muted ? 'Unmute' : 'Mute'}>{muted ? '🔇' : '🔊'}</button>
          </div>
        )}
      </div>

      <div className="detail">
        {saved && <p className="pbp">{h.season ? `${h.league.toUpperCase()} ${h.season}${h.week ? ` · Week ${h.week}` : ''} · ` : ''}{h.imported ? `Archived ${h.league.toUpperCase()} highlight · ${h.date}` : `Replay from ${h.date} · analysis and reactions are simulated.`}
          {h.source?.url && <> <a href={h.source.url} target="_blank" rel="noreferrer">{h.source.channel || 'Watch source'}</a></>}
          {h.source?.play_by_play_url && <> · <a href={h.source.play_by_play_url} target="_blank" rel="noreferrer">{h.league === 'mlb' ? 'MLB play-by-play' : 'ESPN play-by-play'}</a></>}
        </p>}
        <div className="replay-timestamps">
          <span>Event time: <time className="original-play-time" dateTime={h.occurred_utc ?? undefined} title={h.occurred_utc ?? undefined}>{eventTime(h.occurred_utc)}</time></span>
          <span>{h.timestamp_source ?? 'Exact source time unavailable'}</span>
          {h.published_utc && <span>Published by {sourceName}: <time className="published-time" dateTime={h.published_utc}>{eventTime(h.published_utc)}</time></span>}
          {h.received_utc && <span>{kind === 'live_capture' ? 'Captured by BigPlays' : kind === 'official_upload' ? 'Imported by BigPlays' : 'Replay received'}:{' '}
            <time className="import-time" dateTime={h.received_utc}>{eventTime(h.received_utc)}</time></span>}
          {h.social_enrichment && <span className="social-enrichment" data-status={h.social_enrichment.status}>
            Social evidence: {h.social_enrichment.status === 'matched'
              ? `${h.social_enrichment.post_count ?? h.social_enrichment.sources?.length ?? 0} matched posts`
              : 'no matching posts found'}{h.social_score != null ? ` · social score ${h.social_score.toFixed(2)}` : ''}
          </span>}
          {h.video_end != null && <span>Source video: {h.video_start ?? 0}s–{h.video_end}s{!h.imported && ' · scores include the recorded conversion'}</span>}
        </div>
        {!h.imported && <div className="card">
          <div className="card-title">Reddit reactions</div>
          <p>{h.social_assessment?.rationale || (h.social_assessment?.status === 'insufficient_evidence'
            ? 'Not enough distinct reactions to judge this play.' : h.social_assessment?.status === 'play_corrected'
            ? 'The play was corrected; its earlier reaction score has been cleared.'
            : 'No Reddit assessment yet. Video capture does not depend on social reactions.')}</p>
          {h.social_assessment?.confidence !== undefined && <p>Confidence: {Math.round(h.social_assessment.confidence * 100)}%
            {' · '}{h.social_assessment.reaction?.replaceAll('_', ' ')}</p>}
          {h.social_assessment?.sources?.map(source => <a key={source.id} href={source.url} target="_blank" rel="noreferrer">Comment {source.id}{' '}</a>)}
        </div>}
        <div className="detail-head">
          <div>
            <div className="detail-game">
              <span className="team" style={{ ['--c' as string]: h.away_color }}>{h.away} <b>{h.away_score}</b></span>
              <span className="at">@</span>
              <span className="team" style={{ ['--c' as string]: h.home_color }}>{h.home} <b>{h.home_score}</b></span>
              <span className="clock mono">{gamePhase(h)}</span>
            </div>
            <h1 className="headline">{h.title}</h1>
            <p className="pbp">{h.description}</p>
          </div>
          {!h.imported && <HypeMeter value={hypeOf(h)} size={92} stroke={8} />}
        </div>

        <div className="chips">
          {(h.reasons ?? []).map(r => <span key={r} className={`chip reason ${r}`}>{REASON_LABEL[r] ?? r}</span>)}
          {(h.tags ?? []).map(t => <span key={t} className="chip tag">#{t.replace(/\s+/g, '')}</span>)}
        </div>

        {!h.imported && <div className="detail-grid">
          <div className="card">
            <div className="card-title"><span className="claude-dot" /> {h.demo ? 'Demo judgment' : 'Claude judgment'}</div>
            <p className="rationale">{h.llm?.rationale ?? 'Heuristics only (LLM disabled).'}</p>
            <div className="scores">
              <HypeBar value={h.base_score ?? 0} label="heuristic base" />
              <HypeBar value={h.social_score ?? 0} label="social burst" />
              <HypeBar value={h.llm?.hype_score ?? 0} label="LLM hype" />
              <HypeBar value={h.combined_score ?? 0} label="combined" />
            </div>
          </div>
          <div className="card">
            <div className="card-title">{h.demo ? 'Scripted replay context' : 'Retrieved context (RAG)'}</div>
            <ul className="quotes">
              {(h.commentary ?? []).map((c, i) => <li key={i}><span className="q-src">PBP</span>{c}</li>)}
              {(h.social ?? []).map((c, i) => <li key={`s${i}`}><span className="q-src social">SOC</span>{c}</li>)}
              {!(h.commentary?.length || h.social?.length) && <li className="muted">no retrieved context for this event</li>}
            </ul>
          </div>
        </div>}

        <div className="storage mono">
          <span className="muted">stored →</span> {h.storage_uri ?? (h.file ? `file://data/clips/${h.file}` : '—')}
          <span className="muted"> · event {h.event_id}</span>
        </div>
      </div>
    </section>
  )
}
