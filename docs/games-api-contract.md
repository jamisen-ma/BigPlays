# Games / Play-by-play API contract (v1)

This is the shared contract between the data-layer, API, and frontend agents. Change it only via the lead.
Leagues: `nfl`, `mlb`. All times are ISO-8601 UTC with a `Z` suffix. Unknown values are `null` (never guessed).

## GET /api/games?league=nfl|mlb&date=YYYYMMDD
`date` defaults to today in America/Los_Angeles. For NFL, the API may also accept `week=N&season=YYYY`.
```json
{ "ok": true, "league": "mlb", "date": "20260929", "updated_utc": "...", "games": [Game] }
```

### Game
```json
{
  "game_id": "401907924", "league": "mlb",
  "status": "pre" | "in" | "post", "status_detail": "Top 7th" | "Final" | "7:05 PM",
  "start_utc": "...", "venue": "Yankee Stadium", "broadcast": "NBC",
  "period": 7, "period_label": "Top 7" | "Q3", "clock": "5:13" | null,
  "away": Team, "home": Team,
  "situation": MlbSituation | NflSituation | null,
  "last_play_text": "..." | null,
  "clip_count": 3, "viral_count": 2
}
```
- **Team:** `{ "id", "abbr", "name", "short_name", "logo", "color", "alt_color", "score": 5 | null, "record": "90-70" | null, "winner": bool | null }`
- **MlbSituation:** `{ "balls", "strikes", "outs", "on_first": bool, "on_second": bool, "on_third": bool, "batter": "Name" | null, "pitcher": "Name" | null }`
- **NflSituation:** `{ "down", "distance", "yard_line_text": "GB 35", "possession": "GB", "is_red_zone": bool, "down_distance_text": "3rd & 4 at GB 35" }`

## GET /api/games/{league}/{game_id}
```json
{
  "ok": true, "game": Game, "linescore": Linescore, "updated_utc": "...",
  "plays": [Play],
  "clips_unmatched": [ClipRef]
}
```
- `plays` is chronological (oldest first). The frontend reverses them for "latest first" if it wants.
- `clips_unmatched` holds clips for this game that couldn't be tied to a specific play.
- **Linescore:** `{ "periods": [ {"label": "1", "away": 0, "home": 1} ], "totals": {"away": {"R":5,"H":9,"E":0} | {"score":21}, "home": {...}} }`

### Play
```json
{
  "play_id": "401907924123", "sequence": 123,
  "period": 7, "period_label": "Top 7" | "Q3", "clock": "5:13" | null,
  "text": "Aaron Judge homered to left (412 ft), Soto scored.",
  "type": "Home Run", "scoring": true, "team_abbr": "NYY" | null,
  "away_score": 3, "home_score": 5, "wallclock_utc": "..." | null,
  "is_key_play": bool,
  "mlb": { "batter", "pitcher", "balls", "strikes", "outs", "pitch_count" } | null,
  "clip": ClipRef | null,
  "viral": bool,
  "viral_reason": "clip + social score 0.82" | null
}
```

### ClipRef
```json
{ "event_id", "title", "video_url", "poster_url", "duration_seconds",
  "source_kind": "live_capture" | "replay" | "official_upload",
  "social_score": 0.0 | null, "occurred_utc", "published_utc" }
```
`video_url` and `poster_url` are URLs the browser can load directly (the same scheme `/api/highlights` uses today).

## Live updates
The frontend polls `/api/games` every 15 s and the game detail every 8 s while `status == "in"`.
The backend caches ESPN responses (scoreboard ~10 s, summary ~8 s for live games, longer for final games).
Optional: an SSE event `game_update {league, game_id}` on the existing event stream.

## Viral
A play is `viral` when a saved clip is attached to it (the pipeline only clips big plays). `viral_reason` explains why.
Social scores can raise a play's rank, but never gate the attachment.

## v1.1 additions (as implemented)
- `play.related_play_ids` (MLB): every ESPN pitch/event id in the at-bat. Clips link on these too.
- `play.mlb.pitch_count` is the number of pitches in the at-bat. `play.mlb.pitcher_pitch_count` is the pitcher's cumulative total (the broadcast "P:").
  `mlb` balls/strikes are pre-pitch (before the deciding pitch). `outs` is after the play.
- `play.nfl`: `{down_distance_text, yards, drive_id, end_down_distance_text}`.
- MLB `period_label`: "Top N" | "Bottom N" | "Mid N" | "End N". It is null for pre/post games (use `status_detail`).
- Unfinished MLB at-bats are not emitted as plays; the live count is in `game.situation`.
- `play.alternate_clips: [ClipRef]` when more than one clip maps to a play.
- `video_url` is null if the file is missing. `poster_url` may be a YouTube thumbnail.
- Scoreboard `viral_count` for MLB is approximate. Detail counts are exact, so show `clip_count` on cards.
- MLB clip matching adds a deterministic game-state rule (inning, half, score, pre-pitch count, outs). It must match exactly one play; the batter surname in the clip text breaks ties. No time-based matching.
- SSE `game_update {league, game_id}` fires when a game gets a new clip.
- Errors: `{ok:false, error}` with 400/404/502/503. Unknown `/api/*` paths return a JSON 404.
- MLB `play.sequence` is the 1-based position in ESPN's feed, because ESPN's MLB sequenceNumber restarts each at-bat. NFL uses ESPN's sequenceNumber.
- Real MLB play ids are 19 digits (e.g. "4019079650001990057"). Treat ids as opaque strings.
