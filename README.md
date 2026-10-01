# BigPlays

An ESPN-style scoreboard and play-by-play for live NFL, CFB, NBA and MLB games, with video
clips attached to the plays people are actually going crazy about.

BigPlays records live broadcasts into a rolling buffer, follows ESPN's
play-by-play, finds each big play in the video, and cuts a clip in about 40
seconds. Before a clip is published, it checks the game's Reddit thread for real
fan hype and asks a local LLM whether fans are reacting to *that* play. Clips land
in a persistent library and show up inline on the play-by-play in the web app and
the Expo iPhone app.

## How it works

```
ESPN play-by-play ──► big-play filter ──► align play to recorded video ──► cut clip (held)
                                                                                │
Reddit game thread (RSS) ──► hype score ──► local LLM (qwen3:4b) ──► publish or skip
                                                                                │
                                      SQLite library + MP4s ──► web app / Expo app
```

- **Live capture.** `bigplays/orchestrator/live_agent.py` keeps a 30-minute rolling
  HLS buffer per game and reconnects on stalls or gaps. It reads the on-screen
  scorebug with macOS Vision OCR.
  - **Football** plays are matched by quarter and game clock.
  - **MLB** plays are matched by batter, pitcher, pitch count and balls-strikes,
    confirmed by the pitch-count change or the pitch-speed graphic. It handles
    count resets, inning-ending outs and per-pitcher pitch-count offsets, and
    refuses ambiguous matches instead of guessing.
  - Clips are cut with stream copy (about 0.3 s once the window is buffered).
- **Hype gate.** `bigplays/orchestrator/hype_gate.py` scores comments from 5 s
  before to 30 s after the play appears on the broadcast. It looks at all-caps
  spam, stretched words ("OMGGGG"), hype phrases, and comment bursts versus the
  previous 10 minutes. Then `qwen3:4b` running in Ollama gives a verdict.
  - Passing clips publish immediately.
  - Rejected clips stay hidden for 6 h in case you want to publish one manually.
  - Home runs, walk-offs, late go-ahead runs and touchdowns skip the wait.
  - If Reddit is unavailable, the clip publishes after a 90 s fallback.
- **Reddit reactions.** `bigplays/ingest/reddit_rss.py` reads Reddit's official
  public RSS feeds only: no login, no API key, no scraping of HTML. It finds each
  game's thread in the team subreddits, polls the newest comments within Reddit's
  published rate limit (about 1 request per minute), and matches comments to
  clips.
- **Official highlights (backup).** `bigplays/ingest/mlb_archive.py` imports MLB's
  official individual play videos. These fill in plays the live buffer missed, and
  link them to the exact pitch when MLB provides the ID.
- **Games API.** `bigplays/ingest/gamecast.py` normalizes ESPN scoreboards and
  play-by-play. `bigplays/storage/play_links.py` attaches clips to plays by ESPN
  play ID and never by time.

## How viral plays are picked

Every play goes through up to four checks. A play has to get through them all to
show up as a viral clip.

1. **Is it a big play at all?** From ESPN's play-by-play:
   - **MLB:** hits, scoring plays, strikeouts, and defensive plays (double plays,
     caught stealing, pickoffs).
   - **NFL:** touchdowns, field goals, turnovers, and gains of 20+ yards.

   Everything else, like routine groundouts or short runs, is ignored. Plays that
   pass get clipped right away and held, so the clip is ready the moment it's
   approved.
2. **Is it can't-miss?** Home runs, grand slams, walk-offs, go-ahead or tying runs
   in the 7th inning or later, touchdowns, interceptions and lost fumbles publish
   immediately. No waiting on fans.
3. **Are fans losing it?** For everything else, BigPlays reads the Reddit game
   thread from just before the play to 30 seconds after it airs and scores the
   reaction:
   - ALL-CAPS comments and stretched-out words like "OMGGGG" or "NOOOO"
   - "!!!", hype phrases ("WHAT A", "LFG", "HE GONE"), team cheers like "LFGSD", and 🔥😱
   - mentions of the player or team
   - **a burst:** how many more comments per minute than the thread's normal pace
     over the last 10 minutes. A spike means something just happened.

   A low score means the clip is skipped.
4. **Does the AI agree?** If the score is high enough, up to 25 of those comments
   (text only, no usernames) go to a local LLM (`qwen3:4b` in Ollama). It decides
   whether fans are hyped about *this* play, or about something else like a pitching
   change or an earlier moment. On a "yes" the clip publishes instantly, with a few
   fan quotes attached.

**Safety nets.**
- **Skipped clips** stay hidden for 6 hours, in case you want to publish one by
  hand.
- **Reddit unavailable** (no thread found, or rate-limited): the clip publishes
  anyway after about 90 seconds, so nothing is lost.
- **Switching the filter:** `SOCIAL_CLIP_GATE=off` turns the fan check off and
  clips every play that passes step 1.

Example from the CHC@SD Wild Card game:

| Play | Fan score | AI | Result |
|---|---|---|---|
| Conforto strikes out to end the game | 0.87, comments at 3.9× normal pace | viral | published |
| Suzuki singles in the 9th | 0.23 | not asked | skipped |
| Busch strikes out | 0.76 | not viral (fans were talking about the pitcher's return) | skipped |

**Measured on 2026-09-29** (BOS@NYY and CHC@SD Wild Card games): 22 live clips
arrived 32–57 s after the play (median 40 s), with no wrong clips found. Most of
that delay is broadcast lag plus the footage kept after the play.

<img width="1728" height="1023" alt="Screenshot 2026-09-30 at 6 15 57 PM" src="https://github.com/user-attachments/assets/2f6880d4-dce1-4afa-9522-f0cacaacabc0" />
<img width="1722" height="1075" alt="Screenshot 2026-09-30 at 6 16 07 PM" src="https://github.com/user-attachments/assets/779d2395-b50f-4f63-9a87-f39adfc669b5" />
<img width="1728" height="1117" alt="Screenshot 2026-09-30 at 6 16 15 PM" src="https://github.com/user-attachments/assets/47a1f58e-6dab-4d49-b1ca-2e51fbba0ea1" />
<img width="1728" height="1117" alt="Screenshot 2026-09-30 at 6 16 21 PM" src="https://github.com/user-attachments/assets/f422b50c-2a36-4241-80a0-049fbfa585aa" />

## Apps

- **Web app** (`frontend/`, React + Vite), served by the backend at
  http://127.0.0.1:8000.
  - Scores: NFL/MLB tabs, a date picker, and live cards with the base diamond,
    count and outs, or down and distance.
  - Game pages: linescore, play-by-play grouped by inning or quarter, viral plays
    with inline clips, a "viral only" filter, and fan reactions.
  - Clips autoplay muted as you scroll, Instagram-style, one at a time.
- **iPhone app** (`mobile/`, Expo SDK 57): the same screens in React Native. Run
  it with Expo Go or in the browser. See [mobile/README.md](mobile/README.md).

## Running it

Requirements: Python 3.12+ and Node 20+. You also need [Ollama](https://ollama.com)
with `ollama pull qwen3:4b` for the hype gate, and macOS for the scorebug OCR
(`scripts/scoreboard_ocr.swift`). ffmpeg ships with `imageio-ffmpeg`.

```bash
python3 -m venv .venv-local && .venv-local/bin/pip install -r requirements.txt
cp env.example .env
npm install --prefix frontend && npm run build --prefix frontend
```

Start the services, each in its own terminal (or all together with `process-compose up`):

```bash
# API, web app, Reddit poller, MLB highlight importer
.venv-local/bin/python -m bigplays.main server run --host 127.0.0.1 --port 8000

# continuous live capture + clipping (discovers games automatically)
.venv-local/bin/python -m dotenv -f .env run --override -- \
  .venv-local/bin/python -m bigplays.orchestrator.live_agent
```

The live agent needs a video source it can record. See
[docs/stream-integration.md](docs/stream-integration.md) for the resolver
settings, and only record streams you're allowed to record.

Import official MLB highlights for a date by hand:

```bash
.venv-local/bin/python -m bigplays.ingest.mlb_archive --date 2026-09-29
```

Run the iPhone app in a browser on the Mac:

```bash
cd mobile && npm install
EXPO_PUBLIC_API_URL=http://127.0.0.1:8000 npx expo start --web --port 8082
```

## Library

Clip metadata lives in SQLite at `data/clips.sqlite3`, and the MP4s and posters
are in `data/clips/`. Both survive restarts, and stable IDs prevent duplicates.
Every clip keeps its original play time separate from its publish and import
times, and records its source (`live_capture`, `official_upload`) and capture
latency. `data/` is gitignored, so back up or copy it yourself. For a running
server, use the SQLite backup API:

```bash
.venv-local/bin/python -c "from pathlib import Path; from bigplays.config import settings; from bigplays.storage.catalog import catalog_for; catalog_for(settings.clips_dir, settings.database_path).backup(Path('data/backups/clips.sqlite3'))"
```

## API

- `GET /api/games?league=nfl|mlb&date=YYYYMMDD`: scoreboard with clip counts
- `GET /api/games/{league}/{game_id}`: game, linescore and play-by-play with clips attached
- `GET /api/highlights`: the full clip library
- `GET /api/social/feed?league=` / `GET /api/social/status` / `GET /api/social/play/{clip_id}`: fan reactions
- `GET /api/stream`: SSE (`hello`, `highlight`, `highlight_update`, `game_update`, …)
- `GET /api/status`
- `/clips/<file>`: videos and posters

Response shapes are in [docs/games-api-contract.md](docs/games-api-contract.md).

## Config

All settings are environment variables; see `env.example`. The main ones:

- **Capture:** `AGENT_BUFFER_MINUTES`, `AGENT_BUFFER_MAX_MB`, `ESPN_POLL_SECONDS`, `CLIPS_DIR`, `DATABASE_PATH`
- **Hype gate:** `SOCIAL_CLIP_GATE` (`hype` | `off` | `legacy`), `SOCIAL_HYPE_WINDOW_SECONDS`,
  `SOCIAL_HYPE_FALLBACK_SECONDS`, `SOCIAL_HYPE_THRESHOLD`, `SOCIAL_HYPE_PENDING_TTL_HOURS`
- **Local LLM:** `SOCIAL_LLM_PROVIDER=ollama`, `SOCIAL_LLM_MODEL=qwen3:4b`, `OLLAMA_BASE_URL`, `SOCIAL_LLM_CALLS_PER_HOUR`
- **Reddit:** `REDDIT_RSS_ENABLED`, `REDDIT_RSS_USER_AGENT`, `REDDIT_RSS_MIN_INTERVAL_SECONDS`, `REDDIT_RSS_MAX_THREADS`
- **MLB highlights:** `MLB_HIGHLIGHTS_ENABLED`, `MLB_HIGHLIGHTS_POLL_SECONDS`

## Tests

```bash
.venv-local/bin/python -m pytest -q          # backend (302 tests)
npm run build --prefix frontend
node frontend/tests/games.mjs                # scoreboard + play-by-play (mocked API)
node frontend/tests/video-frame.mjs          # clip frame sizing + autoplay
node mobile/tests/web-screens.mjs            # Expo screens at 390px (needs the web server on :8082)
```

## Docs

- [Games API contract](docs/games-api-contract.md)
- [Reddit reactions and hype gate](docs/reddit-setup.md)
- [Social data options](docs/social-data-options.md)
- [Mobile plan](docs/mobile-plan.md) and [mobile/README.md](mobile/README.md)
- [Stream integration](docs/stream-integration.md)
- [Cloud architecture plan](docs/cloud-architecture-plan.md) and [24/7 deployment](docs/cloud.md)

## Known limitations

- ESPN's endpoints are unofficial and can change without notice.
- MLB alignment is validated on NBC's scorebug. Other broadcasters may make the
  agent refuse plays (safe, but no clip). Events between pitches, such as stolen
  bases and passed balls, can't be aligned from video.
- Reddit's RSS feeds only return the newest ~100 comments, have no vote scores,
  and allow about 1 request per minute. A gated clip therefore takes about 70 s
  to 2 min to publish.
- Everything runs on one machine that has to stay awake during games. Don't expose
  the server without authentication in front of it.

MIT
