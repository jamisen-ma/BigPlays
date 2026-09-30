import { useEffect, useState } from 'react'
import type { BaseballState, League } from '../types'
import { gamePhase } from './util'

type Play = BaseballState & { play_id: string; period: number; clock: string; text: string; status: string; interesting: boolean }
type Game = { game: { game_id: string; name: string; league?: League }; buffer_start: string | null; buffer_end: string | null;
  last_play_check: string | null; error: string | null; clock_observations: number; plays: Play[];
  reddit?: { status: string; thread_url?: string; last_checked?: string } }
type Status = { running: boolean; enabled: boolean; matched_games: number; max_games: number; error?: string; games: Game[];
  reddit?: { status: string; missing: string[]; llm_provider?: string; llm_model?: string; source?: string; clip_gate?: boolean } }

export function AgentMonitor() {
  const [status, setStatus] = useState<Status | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let disposed = false
    const poll = async () => {
      try {
        const response = await fetch('/api/agent', { signal: AbortSignal.timeout(10000) })
        const data = await response.json()
        if (!disposed) setStatus(data)
      } catch { if (!disposed) setStatus(null) }
    }
    void poll()
    const timer = setInterval(poll, 5000)
    return () => { disposed = true; clearInterval(timer) }
  }, [])
  async function action(path: string, body: object) {
    setBusy(true)
    try {
      const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const data = await response.json()
      if (!data.ok) throw new Error(data.error)
      setMessage(data.message || (data.enabled ? 'Monitor resumed.' : 'Monitor paused.'))
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Request failed') }
    finally { setBusy(false) }
  }
  return <section className="live-stream agent-monitor">
    <h3>Background play monitor</h3>
    <p>Reddit + LLM: {status?.reddit?.status === 'needs_credentials' ? `Not connected · missing ${status.reddit.missing.join(', ')}.`
      : status?.reddit?.status === 'ready' ? `${status.reddit.source === 'browser' ? 'Public browser' : 'API'} configured · check each game for access status.` : 'Disabled'}</p>
    {status?.reddit?.llm_model && <p>Reaction model configured: {status.reddit.llm_model}
      {status.reddit.llm_provider === 'ollama' ? ' · local Ollama · no LLM API key needed' : ' · Anthropic API'}</p>}
    <p>{!status?.running ? 'Monitor is offline.' : status.enabled
      ? `Running continuously · ${status.games.length}/${status.max_games} games archived · ${status.matched_games} matched streams`
      : 'Paused'}</p>
    <p>{status?.reddit?.clip_gate
      ? 'Initial play filter → game-thread reactions → local model approval → rewind and cut. Missing or unclear reactions hold automatic clips; recording continues.'
      : 'Initial play filter → rewind to the matching on-screen clock and cut.'}</p>
    <p>Fan approval means highlight potential, not proven virality. Reviews wait 90 seconds for reactions and may recheck after 4 minutes.</p>
    {status?.running && <button disabled={busy} onClick={() => void action('/api/agent/control', { enabled: !status.enabled })}>
      {status.enabled ? 'Pause monitor' : 'Resume monitor'}</button>}
    {status?.error && <p role="status">{status.error}</p>}
    {status?.games?.map(game => <div key={game.game.game_id}>
      <h4>{game.game.name}</h4>
      <p>Reddit: {game.reddit?.status.replaceAll('_', ' ') || 'Not connected'}
        {game.reddit?.thread_url && <> · <a href={game.reddit.thread_url} target="_blank" rel="noreferrer">Game thread</a></>}</p>
      <p>{game.buffer_start && game.buffer_end
        ? `Recorded ${new Date(game.buffer_start).toLocaleTimeString()} – ${new Date(game.buffer_end).toLocaleTimeString()}` : 'Starting timestamped recording…'}
        {' · '}{game.clock_observations} clock checks</p>
      {game.last_play_check && <p>Last play check: {new Date(game.last_play_check).toLocaleTimeString()}</p>}
      {game.error && <p role="status">{game.error}</p>}
      <details><summary>Recent plays — select the play to rewind and clip</summary>
        {[...game.plays].reverse().map(play => <div key={play.play_id} className="agent-play">
          <p>{game.game.league === 'mlb' ? gamePhase({ ...play, league: 'mlb', status: undefined }) : `Q${play.period} · ${play.clock}`} · {play.text}</p>
          <p>{play.status}</p>
          <button disabled={busy || !status.enabled || ['clipped', 'outside recorded history', 'missing recorded segments'].includes(play.status)}
            onClick={() => void action('/api/agent/clip', { game_id: game.game.game_id, play_id: play.play_id })}>Cut manually · skip hype check</button>
        </div>)}
      </details>
    </div>)}
    {message && <p role="status">{message}</p>}
  </section>
}
