import { FormEvent, useEffect, useRef, useState } from 'react'
import Hls from 'hls.js'
import type { League } from '../types'

export function LiveStream() {
  const [games, setGames] = useState<{ game_id: string; league: League; name: string; status: string; url: string | null }[]>([])
  const [discoveryError, setDiscoveryError] = useState('')
  const [discoveryWarnings, setDiscoveryWarnings] = useState<string[]>([])
  const [scoreboardsOk, setScoreboardsOk] = useState(false)
  const [checkedAt, setCheckedAt] = useState('')
  const [upcoming, setUpcoming] = useState<{ game_id: string; league: League; name: string; starts_at: string; status: string; url: string | null }[]>([])
  const [showSchedule, setShowSchedule] = useState(false)
  const video = useRef<HTMLVideoElement>(null)
  const [url, setUrl] = useState('')
  const [source, setSource] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [record, setRecord] = useState(true)
  const [recording, setRecording] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    let disposed = false
    async function discover() {
      try {
        const data = await (await fetch('/api/live-streams', { signal: AbortSignal.timeout(20000) })).json()
        if (!disposed) {
          setGames(data.games || [])
          setUpcoming(data.upcoming || [])
          setScoreboardsOk(Boolean(data.scoreboards_ok))
          setCheckedAt(data.checked_at || '')
          setDiscoveryWarnings((data.warnings || []).map((warning: { error: string }) => warning.error))
          setDiscoveryError(data.ok ? '' : `${data.stage}: ${data.error}`)
        }
      } catch { if (!disposed) {
        setDiscoveryError('discovery: Unable to refresh live games. Retrying automatically.')
        setGames([]); setUpcoming([]); setScoreboardsOk(false); setDiscoveryWarnings([])
      } }
    }
    void discover()
    const timer = setInterval(discover, 60000)
    return () => { disposed = true; clearInterval(timer) }
  }, [])

  useEffect(() => {
    let disposed = false
    const poll = async () => {
      try {
        const data = await (await fetch('/api/recording', { signal: AbortSignal.timeout(10000) })).json()
        if (!disposed) { setRecording(data.recording); if (data.error) setError(`record: ${data.error}`) }
      } catch { /* A subsequent action reports connection errors. */ }
    }
    void poll()
    const timer = setInterval(poll, 5000)
    return () => { disposed = true; clearInterval(timer) }
  }, [])

  useEffect(() => {
    const el = video.current
    if (!el || !source) return
    let hls: Hls | undefined
    if (Hls.isSupported()) {
      hls = new Hls({ manifestLoadingTimeOut: 30000, fragLoadingTimeOut: 30000 })
      hls.loadSource(source)
      hls.attachMedia(el)
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) setError(`playback: ${data.details}. Try resolving the event again.`)
      })
    } else if (el.canPlayType('application/vnd.apple.mpegurl')) {
      el.src = source
    } else setError('playback: This browser does not support HLS')
    return () => { hls?.destroy(); el.removeAttribute('src'); el.load() }
  }, [source])

  async function action(path: string, body = {}) {
    setBusy(true); setError(''); setMessage('')
    try {
      const response = await fetch(path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), signal: AbortSignal.timeout(60000),
      })
      const data = await response.json()
      if (!response.ok || !data.ok) throw new Error(`${data.stage || 'request'}: ${data.error || 'Request failed'}`)
      if (data.proxiedUrl) setSource(data.proxiedUrl)
      if (typeof data.recording === 'boolean') setRecording(data.recording)
      if (data.file) setMessage('Highlight saved. It will appear in Incoming plays shortly.')
    } catch (err) { setError(err instanceof Error ? err.message : 'Request failed') }
    finally { setBusy(false) }
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    void action('/api/stream', { url: url.trim(), record })
  }
  return <section className="live-stream">
    <h3>Live source</h3>
    <p>Live MLB / NBA / NFL / college football (FBS) · refreshed every minute</p>
    {discoveryError && <p role="status">{discoveryError}</p>}
    {discoveryWarnings.map(warning => <p role="status" key={warning}>{warning}</p>)}
    {!discoveryError && checkedAt && !games.length && <p>{scoreboardsOk
      ? 'No MLB, NBA, NFL or college football games are in progress right now.'
      : 'No live games found in the available scoreboards. Some leagues could not be checked.'}</p>}
    {!checkedAt && !discoveryError && <p>Checking live games…</p>}
    {checkedAt && <p>Last checked: {new Date(checkedAt).toLocaleTimeString()}</p>}
    {games.map(game => <div key={`${game.league}-${game.game_id}`}>
      <span>{game.name} · {{ matched: 'Stream found', unmatched: 'No matching stream listing',
        ambiguous: 'Multiple stream listings', catalog_unavailable: 'Live · stream catalog unavailable' }[game.status] || game.status}</span>{' '}
      {game.url && <button disabled={busy || recording} onClick={() => {
        setUrl(game.url!); void action('/api/stream', { url: game.url, record: true,
          game: { game_id: game.game_id, league: game.league, name: game.name } })
      }}>Watch & record</button>}
      {game.status === 'ambiguous' && <span>Multiple listings; select an event URL below.</span>}
    </div>)}
    {upcoming.length > 0 && <div>
      <h4>Upcoming games</h4>
      {(showSchedule ? upcoming : upcoming.slice(0, 6)).map(game => <div key={`${game.league}-${game.game_id}`}>
        <p>{game.league === 'ncaaf' ? 'CFB' : game.league.toUpperCase()} · {game.name} · {new Date(game.starts_at).toLocaleString()}
          {' · '}{game.status === 'matched' ? 'Stream listing found' : game.status === 'catalog_unavailable'
            ? 'Stream catalog unavailable' : game.status === 'ambiguous' ? 'Multiple listings' : 'Awaiting stream listing'}</p>
        {game.url && <button disabled={busy || recording} onClick={() => {
          setUrl(game.url!); void action('/api/stream', { url: game.url, record: true,
            game: { game_id: game.game_id, league: game.league, name: game.name } })
        }}>Watch & record</button>}
      </div>)}
      {upcoming.length > 6 && <button onClick={() => setShowSchedule(!showSchedule)}>
        {showSchedule ? 'Show fewer games' : `Show all ${upcoming.length} upcoming games`}
      </button>}
    </div>}
    <form onSubmit={submit}>
      <label htmlFor="event-url">PPV event URL</label>
      <input id="event-url" type="url" required value={url} onChange={e => setUrl(e.target.value)} placeholder="https://ppv.st/live/event-slug" />
      <label><input type="checkbox" checked={record} onChange={e => setRecord(e.target.checked)} /> Record for highlights</label>
      <button disabled={busy || (record && recording)}>{busy ? 'Working…' : 'Load stream'}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {source && <video ref={video} controls muted autoPlay playsInline onError={() => setError('playback: Video could not be loaded')} />}
    {recording && <div className="live-actions">
      <span>Recording · allow at least 20 seconds for the buffer to fill</span>
      <button disabled={busy} onClick={() => void action('/api/recording/clip', { seconds: 14 })}>Manual cut: latest 14 seconds</button>
      <span>For a specific play, use the background monitor’s play list above.</span>
      <button disabled={busy} onClick={() => void action('/api/recording/stop')}>Stop recording</button>
    </div>}
    {message && <p role="status">{message}</p>}
  </section>
}
