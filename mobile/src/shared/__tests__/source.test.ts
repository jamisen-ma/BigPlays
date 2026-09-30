import { describeError, ApiError, poller } from '../api'
import {
  alternateLabels, clipOptions, filterLibrary, highlightPoster, highlightVideo, libraryLeagues, sourceKindOf, sourceLabel, timeAgo,
} from '../source'
import type { Highlight } from '../types'

describe('sourceKindOf / sourceLabel', () => {
  it('prefers explicit source_kind, then infers', () => {
    expect(sourceKindOf({ source_kind: 'official_upload', demo: true })).toBe('official_upload')
    expect(sourceKindOf({ demo: true })).toBe('replay')
    expect(sourceKindOf({ imported: true })).toBe('official_upload')
    expect(sourceKindOf({})).toBe('live_capture')
    expect(sourceKindOf({ source_kind: 'bogus' as never })).toBe('live_capture')
  })
  it('labels', () => {
    expect(sourceLabel('live_capture')).toBe('LIVE CAPTURE')
    expect(sourceLabel('official_upload', 'mlb')).toBe('OFFICIAL UPLOAD')
    expect(sourceLabel('replay', 'nfl')).toBe('NFL REPLAY')
    expect(sourceLabel('replay', 'unknown')).toBe('REPLAY')
  })
})

describe('clip options', () => {
  const a = { event_id: 'a', title: 'Broadcast', source_kind: 'official_upload' as const }
  const b = { event_id: 'b', title: 'Field View', source_kind: 'live_capture' as const }
  const c = { event_id: 'c', title: 'Spanish', source_kind: 'official_upload' as const }
  it('dedupes alternates against the primary', () => {
    expect(clipOptions(a, [a, b, null, b]).map(x => x.event_id)).toEqual(['a', 'b'])
    expect(clipOptions(a, null)).toEqual([a])
  })
  it('labels by provenance when distinct, else by title', () => {
    expect(alternateLabels([a, b], 'mlb')).toEqual(['OFFICIAL UPLOAD', 'LIVE CAPTURE'])
    expect(alternateLabels([a, c], 'mlb')).toEqual(['Broadcast', 'Spanish'])
  })
})

describe('library', () => {
  const h = (over: Partial<Highlight>): Highlight => ({ event_id: Math.random().toString(36), game_id: 'g', league: 'mlb', title: 't', occurred_utc: null, file: 'x.mp4', ...over })
  it('poster/video paths', () => {
    expect(highlightPoster({ poster: 'a.jpg' })).toBe('/clips/a.jpg')
    expect(highlightPoster({ poster: 'a.jpg', youtube_id: 'abc' })).toBe('https://i.ytimg.com/vi/abc/mqdefault.jpg')
    expect(highlightPoster({})).toBeNull()
    expect(highlightVideo({ file: 'a.mp4' })).toBe('/clips/a.mp4')
    expect(highlightVideo({ file: null })).toBeNull()
  })
  it('leagues NFL/MLB first', () => {
    expect(libraryLeagues([h({ league: 'ncaaf' }), h({ league: 'mlb' }), h({ league: 'nfl' }), h({ league: 'mlb' })])).toEqual(['nfl', 'mlb', 'ncaaf'])
  })
  it('filters playable clips by league, newest first', () => {
    const items = [
      h({ event_id: 'old', occurred_utc: '2026-09-01T00:00:00Z' }),
      h({ event_id: 'new', published_utc: '2026-09-29T00:00:00Z' }),
      h({ event_id: 'nofile', file: null }),
      h({ event_id: 'nfl', league: 'nfl', occurred_utc: '2026-09-20T00:00:00Z' }),
    ]
    expect(filterLibrary(items, 'all').map(x => x.event_id)).toEqual(['new', 'nfl', 'old'])
    expect(filterLibrary(items, 'mlb').map(x => x.event_id)).toEqual(['new', 'old'])
  })
  it('timeAgo', () => {
    const now = Date.parse('2026-09-29T12:00:00Z')
    expect(timeAgo('2026-09-29T11:59:58Z', now)).toBe('just now')
    expect(timeAgo('2026-09-29T11:59:00Z', now)).toBe('1m ago')
    expect(timeAgo('2026-09-29T09:00:00Z', now)).toBe('3h ago')
    expect(timeAgo('2026-09-25T12:00:00Z', now)).toBe('4d ago')
    expect(timeAgo(null, now)).toBe('')
  })
})

describe('api helpers', () => {
  it('describeError names the unreachable URL', () => {
    expect(describeError(new ApiError(0, 'Network error', true), 'http://10.0.0.2:8000')).toMatch(/^Can't reach BigPlays at http:\/\/10\.0\.0\.2:8000/)
    expect(describeError(new ApiError(404, 'Not found'), 'x', 'play-by-play')).toMatch(/404/)
    expect(describeError(new ApiError(502, 'ESPN down'), 'x', 'scores')).toBe("Couldn't load scores (ESPN down).")
  })
  it('poller repeats while load returns true and stops cleanly', async () => {
    jest.useFakeTimers()
    let n = 0
    const p = poller(async () => ++n < 3, () => 1000)
    await Promise.resolve(); await Promise.resolve()
    expect(n).toBe(1)
    for (let i = 0; i < 4; i++) { jest.advanceTimersByTime(1000); await Promise.resolve(); await Promise.resolve() }
    expect(n).toBe(3)
    p.stop()
    jest.useRealTimers()
  })
})
