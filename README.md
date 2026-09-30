# BigPlays

an agent that watches NBA/NFL games and figures out which plays are gonna go viral, then clips them automatically. built this because I got tired of refreshing twitter to find the highlight everyone was talking about.

basically: scoreboard + play-by-play + commentary + social signals go in -> Claude decides if its a "big play" -> ffmpeg cuts the clip out of the live buffer -> clip gets tagged and dropped in S3. theres a react dashboard so you can watch the plays roll in live.

![dashboard](https://img.shields.io/badge/status-works%20on%20my%20machine-green)

## whats in here

- `bigplays/ingest/` - polls ESPN scoreboards for NBA and NFL, plus some social stuff (reddit is stubbed, theres a mock social burst generator for demos)
- `bigplays/orchestrator/highlight_detector.py` - the rules part. lead change, big swing, clutch time, social spike etc get a base score
- `bigplays/llm/reasoner.py` - Claude (thru langchain) gets the context and returns verdict / hype score / tags / a title. rules + llm get blended
- `bigplays/media/` - rolling HLS recorder into .ts segments and a clipper that cuts a window around the event by wall clock time
- `bigplays/storage/` - local + S3
- `bigplays/assembly/` - stitches clips into a reel at half / final
- `bigplays/server/` - fastapi. serves the api, the SSE stream and the built frontend
- `bigplays/demo/` - demo mode, see below
- `frontend/` - the dashboard (react + vite)

## running it

python 3.10+. ffmpeg on your PATH is nice but not required, it falls back to the one bundled w/ imageio-ffmpeg (no ffprobe in that one though).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env   # put your ANTHROPIC_API_KEY in there, S3 stuff is optional
```

then in separate terminals:

```bash
# records the broadcast into a rolling buffer
python -m bigplays.main recorder start --stream-url "$STREAM_URL"
# no stream handy? --test uses a public test hls playlist so you can try the buffer + clipper
python -m bigplays.main recorder start --test
# cut the last 14s out of the buffer by hand (shows up in the dashboard feed)
python -m bigplays.main recorder clip --last 14

# the actual agent (ingest -> detect -> clip -> store)
python -m bigplays.main agent run --league nba

# api + dashboard
python -m bigplays.main server run
```

frontend dev server w/ hot reload (proxies /api and /clips to :8000):

```bash
cd frontend && npm install && npm run dev
```

or `npm run build` once and fastapi will serve it at http://localhost:8000.

## demo mode (no games on)

most of the year theres no live nba when I want to show this off, so `--demo` replays real plays from last season through the exact same pipeline. every 7-14 sec it "detects" a play, runs the stages (ingest -> rag retrieve -> heuristics -> social -> claude -> ffmpeg -> s3) and drops a clip + json sidecar into `data/clips/` same as prod would.

```bash
python -m bigplays.main server run --demo
# NFL only: five historical highlights, arriving with live-style pacing
python -m bigplays.main server run --demo --demo-league nfl
```

Set `DEMO_MODE=true` and `DEMO_LEAGUE=nfl` in `.env` to keep NFL replay enabled
on restart. Replay cards retain the original ESPN event time, game date and
play-by-play link; `received_utc` records their arrival in the demo feed. The
dashboard labels archived footage and simulated analysis, and shows an animation
fallback if an embedded video fails to load. NFL scores and clocks are checked
against ESPN's historical play-by-play.

For **2026 regular-season Week 3**, import the individual NFL.com highlight
videos across all 16 games. The importer discovers the week's source catalog,
downloads every individual highlight without a count limit, and saves each clip
to the persistent library. Full-game recaps and player compilations are excluded.
Rerunning the command reuses downloaded videos and updates existing database
records by their source IDs:

```bash
# The downloader is included in requirements.txt.
python -m bigplays.ingest.week3_archive --workers 6
DEMO_DATASET=nfl-2026-week3 python -m bigplays.main server run --demo --demo-league nfl
```

Persist `DEMO_MODE=true`, `DEMO_LEAGUE=nfl`, and `DEMO_DATASET=nfl-2026-week3`
in `.env` for restarts. Cards retain ESPN's original UTC play timestamp when the
source video can be matched to a play, separately from import/replay arrival and
source publication time. Unmatched play times remain unavailable; publication
time is never substituted for the time the play happened. The game date is the
scheduled US Eastern date, so Monday-night games retain September 28 even when
the event timestamp is September 29 UTC. Game clocks are ESPN's play event markers;
they do not tick between archived plays. Source scores include the recorded PAT
or two-point conversion. NFL.com imports retain the entire individual source
clip. The original 15 ESPN video windows were reviewed against the footage;
offsets are never inferred from event timestamps. The imported library is replayed
in original event order. Other captures remain on disk; MLB highlights also appear
alongside this NFL archive.

## MLB official highlights

The MLB collector uses MLB's public schedule, Gameday play feed, and official
individual video highlights. Set `MLB_HIGHLIGHTS_ENABLED=true` in `.env` to check
for new clips every minute while the app runs. `MLB_HIGHLIGHTS_POLL_SECONDS`
changes the interval (minimum 30 seconds). The schedule uses the current US
Pacific date and shows upcoming, live, and completed games. Available clips
appear in the same persistent library as NFL Week 3; use the MLB filter or
open `http://127.0.0.1:8000/?league=mlb`.

```bash
python -m bigplays.ingest.mlb_archive --date 2026-09-29
```

MLB's exact video/play GUID supplies the original pitch timestamp when available;
unique descriptive matches are also recorded with their matching method. Missing
event times remain unavailable. The UI shows innings, half-innings, counts before
the pitch, and outs. Videos arrive when MLB publishes them, which can be after the
play finishes. This collector does not depend on football game-clock OCR or a
full-game stream. Repeated imports reuse videos and stable database IDs.

For fan-reaction data, see [the current Reddit and X API options](docs/social-data-options.md).

## persistent highlight library

The library uses SQLite at `data/clips.sqlite3` for clip metadata, source links,
timestamps, and import progress. The actual MP4 videos and JPEG posters live in
`data/clips/`; the database records their filenames. Closing the browser or
restarting the app keeps both the videos and their catalog entries. Demo startup
does not purge saved highlights, and the feed restores the entire saved catalog.
Existing JSON sidecars are imported automatically; deleting a sidecar does not
delete its database entry.

`CLIPS_DIR` changes the video directory. By default the SQLite file sits beside
that directory with a `.sqlite3` suffix; set `DATABASE_PATH` to choose an explicit
database location. Keep these locations on persistent storage. The cloud compose
configuration's `/data` volume contains both by default.

Back up the video directory together with the database. When the server is
stopped, copying both is sufficient. For a running server, use SQLite's backup
API so committed write-ahead-log changes are included:

```bash
python -c "from pathlib import Path; from bigplays.config import settings; from bigplays.storage.catalog import catalog_for; catalog_for(settings.clips_dir, settings.database_path).backup(Path('data/backups/clips.sqlite3'))"
```

Copy `data/clips/` alongside that database backup. Restore both to retain video
playback. The database preserves metadata if a video file is temporarily absent;
the app makes playback available again when the file returns.

## MLB highlights

The MLB importer reads the official MLB schedule, game highlight catalog, and
live play-by-play. It downloads individual play footage, including alternate
angles, into the same persistent SQLite/video library. Pregame lineups,
interviews, full-game recaps, and pitching compilations are excluded.

```bash
python -m bigplays.ingest.mlb_archive --date 2026-09-29
```

Run it again as games progress to pick up newly published highlights. Existing
source IDs and files are reused, so reruns do not duplicate clips or reset their
arrival time. The server can call the same `import_date` function for periodic
updates. Game progress uses innings, half innings, balls, strikes, and outs.
Clip counts describe the moment before the highlighted pitch; current game
counts come from MLB's live linescore.

When MLB supplies a video GUID, the importer links it directly to the original
pitch's `playId` and `startTime`. Other timestamps require an unambiguous play
match or reviewed footage. Publication and download times are stored separately;
unmatched event times remain unavailable. Each clip links back to its MLB source
page and the game's play-by-play.

## older demo footage

the original demo plays are from the 2025-26 nba season and the 2025 nfl season - anunoby's tip in to finish the 29 pt comeback in finals game 4, brunson's floater in the clincher, booker over caruso w/ 0.7 left, KD's revenge 3 on phoenix, reaves' buzzer beater in minnesota, hachimura's corner 3 off lebron, wemby's 12 block game, the jamal cain poster on duren, caleb -> dj moore walk off vs green bay, treylon burks' one hander, shaheed's 95 yd kick return, caleb's 4th down miracle to kmet, nwosu's super bowl pick six. list is in `bigplays/demo/plays.py`, add your own if you want.

couple notes on how the older `highlights` demo footage works (the Week 3 archive
above downloads local MP4s):
- it streams thru youtube's embed player under the hood, nothing gets downloaded (dont wanna deal w/ youtube tos or the leagues lawyers). the player hides all the youtube ui, crops the title bar out and draws its own controls so it looks like the agent cut it. each play has a start/end so you only see the ~10 sec of the actual play, no replays or interviews
- nba's channel lets you embed, nfl's doesnt (error 150), so nfl plays come from fox / nbc / fan uploads. if an owner blocks one later it falls back to a synthetic rendered clip automatically
- to check if a new video id actually embeds theres `frontend/dev/embedtest.html`, copy it into public/ and open it. oembed lies about this so dont trust it
- theres also a synthetic clip renderer (`bigplays/demo/clip_gen.py`, pillow + the ffmpeg that comes with imageio-ffmpeg) that draws little animated players/ball/scoreboard. thats the fallback when footage doesnt load. `python -m bigplays.main demo clips` renders them

## api

- `GET /api/highlights` - the saved catalog, newest arrival first (filtered by the selected demo dataset)
- `GET /api/games` - games being monitored w/ live scores
- `GET /api/status`
- `GET /api/stream` - SSE. events: `hello`, `game_tick`, `pipeline`, `highlight`, `log`
- `POST /api/demo/next` - fire the next demo play now (theres a button for it in the ui too)
- `/clips/<file>` - the mp4s / posters / json

the server also watches CLIPS_DIR for new sidecars so if you run the real agent in another process its highlights show up in the feed too.

## config

everything is env vars, see `env.example`. the important ones:

- `LEAGUES` (nba,nfl), `ESPN_POLL_SECONDS`
- `USE_LLM`, `ANTHROPIC_API_KEY`, `LLM_MODEL`
- `STREAM_URL`, `BUFFER_DIR`, `CLIPS_DIR`, `PRE_ROLL_SECONDS`, `POST_ROLL_SECONDS`
- `DATABASE_PATH`, `ENABLE_S3`, `S3_BUCKET`, `S3_PREFIX`
- `DEMO_MODE`, `DEMO_CLIPS_DIR`, `DEMO_MIN_INTERVAL`, `DEMO_MAX_INTERVAL`

## other stuff

- `python -m bigplays.main ppv list|live|resolve|record` - helpers for finding an HLS url off ppv.to. dont hammer their api
- `python -m bigplays.main assemble reels` - dumps all current clips into one reel.mp4
- `pytest -q` for the tests, `ruff check .` if you care

## known jank

- espn's public endpoints arent official, they change whenever. fine for a project, get a real feed for anything serious
- the scoreboard api doesnt give play by play so "events" are score deltas. the demo plays have the real descriptions baked in
- clip timing depends on your system clock being right, use ntp. segment files are named in utc
- w/ `-c copy` ffmpeg can only split segments on keyframes, so if the source has 10s gops your buffer granularity is 10s not 2
- for a real stream you need one you're allowed to record (ota antenna + hdhomerun works great for local nfl/nba games). not touching the pirate sites
- autoplay is muted (browser rules), hit the speaker icon
- dont expose the dashboard without putting auth in front of it

MIT

## PPV resolver integration

The dashboard discovers NBA, NFL and college football (FBS), matches both teams
to PPV event listings, and supports HLS playback, recording and highlight cuts.
Open http://127.0.0.1:8000 when the local services are running.

The working provider is now `ppv.st`; old `ppv.to` event URLs remain aliases.
The vendored Node resolver retains its original handshake and WASM. Live CFB
resolution, browser playback, recording, and timestamp-aligned play clipping
have been tested. A persistent `agent` process discovers games every minute,
archives two matched games by default, and checks play-by-play every five seconds.
It matches ESPN's quarter/game clock to the on-screen clock, then rewinds the
timestamped archive to cut that play. It keeps working with the dashboard closed.

Use `.venv-local` on this checkout. See [setup, environment variables, deployment,
changed files and validation](docs/stream-integration.md). The background monitor
starts with `process-compose.yaml`. Its play list supports **Cut manually · skip hype check**;
scoring plays, turnovers and big gains are queued for fan-reaction review automatically. **Watch & record**
remains a separate single-game manual recorder. Public deployment requires
authentication with Node kept private. The legacy `agent run` CLI above predates
this integration; use the supervised `bigplays.orchestrator.live_agent` instead.

Automatic clips now require fan-reaction approval: initial play filter → game-thread
reactions → local Ollama (`qwen3:4b`) → timestamp-aligned cut. No LLM API key is
needed. Public-browser collection is enabled but currently blocked by Reddit's
human-verification page, so automatic clips wait while recording continues.
Manual cuts explicitly bypass the hype check. See [configuration, limitations,
and validation](docs/reddit-setup.md).

For a persistent Linux cloud host, see [24/7 deployment](docs/cloud.md).
`compose.cloud.yaml` includes restart policies, persistent storage, a private
resolver, the dashboard and Linux scoreboard OCR. Hosting and working social
access still need to be configured before unattended clipping is operational.
