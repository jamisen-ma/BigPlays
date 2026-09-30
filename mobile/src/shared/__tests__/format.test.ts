import {
  dateLabel, downDistanceText, formatDuration, groupPlays, isDateKey, periodName, playWhen, possessionOf,
  shiftDate, sortGames, statusText, teamColor, todayKey, viralPlays,
} from '../format'
import { linescoreColumns } from '../linescore'
import type { Play, ScoreGame, Team } from '../types'

const team = (abbr: string, over: Partial<Team> = {}): Team => ({
  id: abbr, abbr, name: abbr, short_name: abbr, logo: null, color: '003087', alt_color: null, score: 0, record: null, winner: null, ...over,
})
const game = (over: Partial<ScoreGame> = {}): ScoreGame => ({
  game_id: '1', league: 'mlb', status: 'in', status_detail: null, start_utc: '2026-09-29T23:05:00Z', venue: null, broadcast: null,
  period: 7, period_label: 'Top 7', clock: null, away: team('BOS'), home: team('NYY'), situation: null, last_play_text: null,
  clip_count: 0, viral_count: 0, ...over,
})
let seq = 0
const play = (over: Partial<Play> = {}): Play => ({
  play_id: String(++seq), sequence: seq, period: 1, period_label: 'Top 1', clock: null, text: 'x', type: null, scoring: false,
  team_abbr: null, away_score: 0, home_score: 0, wallclock_utc: null, is_key_play: false, mlb: null, clip: null, viral: false,
  viral_reason: null, ...over,
})

describe('dates', () => {
  it('todayKey is YYYYMMDD in America/Los_Angeles', () => {
    // 2026-09-30 03:00Z is still Sept 29 in LA
    expect(todayKey(new Date('2026-09-30T03:00:00Z'))).toBe('20260929')
    expect(todayKey(new Date('2026-09-30T08:00:00Z'))).toBe('20260930')
    expect(isDateKey(todayKey())).toBe(true)
  })
  it('shiftDate crosses month and year boundaries', () => {
    expect(shiftDate('20260930', 1)).toBe('20261001')
    expect(shiftDate('20260101', -1)).toBe('20251231')
  })
  it('dateLabel marks relative days', () => {
    expect(dateLabel('20260929', '20260929')).toMatch(/^Today · Tue, Sep 29$/)
    expect(dateLabel('20260928', '20260929')).toMatch(/^Yesterday/)
    expect(dateLabel('20260930', '20260929')).toMatch(/^Tomorrow/)
    expect(dateLabel('20261005', '20260929')).toBe('Mon, Oct 5')
  })
  it('isDateKey', () => {
    expect(isDateKey('20260929')).toBe(true)
    expect(isDateKey('2026-09-29')).toBe(false)
    expect(isDateKey(null)).toBe(false)
  })
})

describe('periodName / statusText', () => {
  it('normalizes MLB half-inning labels and NFL fallbacks', () => {
    expect(periodName('Bottom 7', 'mlb')).toBe('Bot 7')
    expect(periodName('Top 7th', 'mlb')).toBe('Top 7')
    expect(periodName('Mid 3', 'mlb')).toBe('Mid 3')
    expect(periodName('End 9', 'mlb')).toBe('End 9')
    expect(periodName('Q3', 'nfl')).toBe('Q3')
    expect(periodName(null, 'nfl', 2)).toBe('Q2')
    expect(periodName(null, 'nfl', 5)).toBe('OT')
    expect(periodName(null, 'mlb', 4)).toBe('Inning 4')
    expect(periodName(null, 'mlb')).toBe('—')
  })
  it('statusText for live / final / halftime', () => {
    expect(statusText(game({ period_label: 'Bottom 2' }))).toBe('Bot 2')
    expect(statusText(game({ status: 'post', status_detail: 'Final/10' }))).toBe('Final/10')
    expect(statusText(game({ status: 'post', status_detail: null }))).toBe('Final')
    expect(statusText(game({ league: 'nfl', period: 3, period_label: 'Q3', clock: '5:13' }))).toBe('Q3 · 5:13')
    expect(statusText(game({ league: 'nfl', status_detail: 'Halftime', period_label: 'Q2' }))).toBe('Halftime')
    expect(statusText(game({ status: 'pre', start_utc: null, status_detail: '7:05 PM' }))).toBe('7:05 PM')
  })
})

describe('sortGames', () => {
  it('orders live, then upcoming, then final, each by start time', () => {
    const out = sortGames([
      game({ game_id: 'f', status: 'post', start_utc: '2026-09-29T17:00:00Z' }),
      game({ game_id: 'p', status: 'pre', start_utc: '2026-09-30T02:00:00Z' }),
      game({ game_id: 'l2', status: 'in', start_utc: '2026-09-30T01:00:00Z' }),
      game({ game_id: 'l1', status: 'in', start_utc: '2026-09-29T23:00:00Z' }),
    ])
    expect(out.map(g => g.game_id)).toEqual(['l1', 'l2', 'p', 'f'])
  })
})

describe('groupPlays', () => {
  const plays = [
    play({ sequence: 3, period_label: 'Bottom 1', play_id: 'c' }),
    play({ sequence: 1, period_label: 'Top 1', play_id: 'a' }),
    play({ sequence: 2, period_label: 'Top 1', play_id: 'b' }),
    play({ sequence: 4, period_label: 'Top 2', play_id: 'd' }),
  ]
  it('groups consecutive plays by period, chronological', () => {
    const g = groupPlays(plays, 'mlb', false)
    expect(g.map(x => x.label)).toEqual(['Top 1', 'Bot 1', 'Top 2'])
    expect(g[0].plays.map(p => p.play_id)).toEqual(['a', 'b'])
  })
  it('latest first reverses groups and plays within them', () => {
    const g = groupPlays(plays, 'mlb', true)
    expect(g.map(x => x.label)).toEqual(['Top 2', 'Bot 1', 'Top 1'])
    expect(g[2].plays.map(p => p.play_id)).toEqual(['b', 'a'])
    expect(new Set(g.map(x => x.key)).size).toBe(3)
  })
  it('does not mutate input', () => {
    const copy = plays.map(p => p.play_id)
    groupPlays(plays, 'mlb', true)
    expect(plays.map(p => p.play_id)).toEqual(copy)
  })
  it('viralPlays keeps plays with a clip', () => {
    const clip = { event_id: 'e', title: 't', video_url: '/clips/e.mp4', poster_url: null, duration_seconds: 5, source_kind: null, social_score: null, occurred_utc: null, published_utc: null }
    expect(viralPlays([play(), play({ clip })]).length).toBe(1)
  })
})

describe('small formatters', () => {
  it('playWhen', () => {
    expect(playWhen(play({ mlb: { batter: null, pitcher: null, balls: 1, strikes: 2, outs: 1, pitch_count: 4 } }), 'mlb')).toBe('1-2, 1 out')
    expect(playWhen(play({ mlb: null }), 'mlb')).toBe('')
    expect(playWhen(play({ clock: '5:13' }), 'nfl')).toBe('5:13')
  })
  it('formatDuration', () => {
    expect(formatDuration(16.3)).toBe('0:16')
    expect(formatDuration(75)).toBe('1:15')
    expect(formatDuration(null)).toBeNull()
    expect(formatDuration(NaN)).toBeNull()
  })
  it('teamColor adds # and falls back', () => {
    expect(teamColor({ color: 'bd3039' })).toBe('#bd3039')
    expect(teamColor({ color: '#003087' })).toBe('#003087')
    expect(teamColor({ color: null })).toBe('#2a3242')
  })
  it('downDistanceText + possession', () => {
    const s = { down: 3, distance: 4, yard_line_text: 'GB 35', possession: 'GB', is_red_zone: false, down_distance_text: null }
    expect(downDistanceText(s)).toBe('3rd & 4 at GB 35')
    expect(downDistanceText({ ...s, down_distance_text: '1st & 10 at CHI 25' })).toBe('1st & 10 at CHI 25')
    expect(downDistanceText({ ...s, down: null })).toBeNull()
    expect(possessionOf(game({ league: 'nfl', situation: s }))).toBe('GB')
    expect(possessionOf(game({ league: 'nfl', status: 'post', situation: s }))).toBeNull()
  })
})

describe('linescoreColumns', () => {
  it('pads MLB to 9 innings with R/H/E', () => {
    const cols = linescoreColumns(game(), {
      periods: [{ label: '1', away: 0, home: 1 }, { label: '2', away: null, home: 2 }],
      totals: { away: { R: 0, H: 3, E: 1 }, home: { R: 3, H: 5, E: 0 } },
    })!
    expect(cols.periods).toHaveLength(9)
    expect(cols.periods[1]).toEqual({ label: '2', away: '–', home: 2 })
    expect(cols.totals.map(t => t.label)).toEqual(['R', 'H', 'E'])
    expect(cols.totals[1]).toEqual({ label: 'H', away: 3, home: 5 })
  })
  it('pads NFL to 4 quarters with a T column falling back to team score', () => {
    const g = game({ league: 'nfl', away: team('GB', { score: 21 }), home: team('CHI', { score: 17 }) })
    const cols = linescoreColumns(g, { periods: [{ label: '1', away: 7, home: 0 }], totals: null })!
    expect(cols.periods).toHaveLength(4)
    expect(cols.totals).toEqual([{ label: 'T', away: 21, home: 17 }])
  })
  it('returns null without periods', () => {
    expect(linescoreColumns(game(), null)).toBeNull()
    expect(linescoreColumns(game(), { periods: [], totals: null })).toBeNull()
  })
})
