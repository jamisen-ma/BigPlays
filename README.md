# BigPlays

an agent that watches live games (MLB, NFL, NBA, college football) and figures out which plays are gonna go viral, then clips them automatically. built this because I got tired of refreshing twitter to find the highlight everyone was talking about.

basically: live stream gets recorded into a rolling buffer -> espn play-by-play tells us a play happened -> ffmpeg cuts it out of the buffer, lined up w/ the on-screen scorebug -> reddit game thread reactions + a local llm decide if its actually hype -> clip gets published. theres a react dashboard (scores + play-by-play w/ clips attached) and an expo app in `mobile/` so you can watch the plays roll in live.

![dashboard](https://img.shields.io/badge/status-works%20on%20my%20machine-green)

## whats in here

- `bigplays/ingest/` - ESPN scoreboards + gamecast play-by-play, MLB's official feeds, and reddit game thread reactions (public rss)
- `bigplays/orchestrator/live_agent.py` - the live agent. finds games, records them, cuts plays, runs the hype gate (`hype_gate.py`)
- `bigplays/orchestrator/highlight_detector.py` - the rules part. lead change, big swing, clutch time, social spike etc get a base score
- `bigplays/llm/reasoner.py` - Claude (thru langchain) gets the context and returns verdict / hype score / tags / a title. rules + llm get blended
- `bigplays/media/` - rolling HLS recorder into .ts segments and a clipper that cuts a window around the event by wall clock time
- `bigplays/storage/` - local + S3
- `bigplays/assembly/` - stitches clips into a reel at half / final
- `bigplays/server/` - fastapi. serves the api, the SSE stream and the built frontend
- `frontend/` - the dashboard (react + vite)
- `mobile/` - expo go app, scores + play-by-play + clips on your phone. see `mobile/README.md`

## running it

python 3.10+. ffmpeg on your PATH is nice but not required, it falls back to the one bundled w/ imageio-ffmpeg (no ffprobe in that one though).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env   # put your ANTHROPIC_API_KEY in there, S3 stuff is optional
```

then start everything w/ process-compose (resolver + api/dashboard on :8000 + the live agent):

```bash
process-compose -f process-compose.yaml -U -u /tmp/bigplays-process-compose.sock up -D
```

thats how it actually runs. more detail in [docs/stream-integration.md](docs/stream-integration.md).

manual tools if you want to poke at pieces by hand:

```bash
# records the broadcast into a rolling buffer
python -m bigplays.main recorder start --stream-url "$STREAM_URL"
# no stream handy? --test uses a public test hls playlist so you can try the buffer + clipper
python -m bigplays.main recorder start --test
# cut the last 14s out of the buffer by hand (shows up in the dashboard feed)
python -m bigplays.main recorder clip --last 14

# the live agent on its own (what process-compose runs)
python -m bigplays.orchestrator.live_agent

# api + dashboard
python -m bigplays.main server run --host 127.0.0.1 --port 8000
```

frontend dev server w/ hot reload (proxies /api and /clips to :8000):

```bash
cd frontend && npm install && npm run dev
```

or `npm run build` once and fastapi will serve it at http://localhost:8000.

## MLB official highlights

The MLB collector uses MLB's public schedule, Gameday play feed, and official
individual video highlights. Set `MLB_HIGHLIGHTS_ENABLED=true` in `.env` to check
for new clips every minute while the app runs. `MLB_HIGHLIGHTS_POLL_SECONDS`
changes the interval (minimum 30 seconds). The schedule uses the current US
Pacific date and shows upcoming, live, and completed games. Available clips
appear in the persistent highlight library; use the MLB filter or
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
restarting the app keeps both the videos and their catalog entries, and the feed
restores the entire saved catalog.
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

## api

- `GET /api/highlights` - the saved catalog, newest arrival first
- `GET /api/games`, `GET /api/games/{league}/{game_id}` - scoreboard + play-by-play w/ clips linked to plays
- `GET /api/social/feed`, `/api/social/play/{clip_id}` - reddit reactions
- `GET /api/status`
- `GET /api/stream` - SSE. events: `hello`, `game_tick`, `pipeline`, `highlight`, `log`
- `/clips/<file>` - the mp4s / posters / json

the server also watches CLIPS_DIR for new sidecars so if you run the real agent in another process its highlights show up in the feed too.

## config

everything is env vars, see `env.example`. the important ones:

- `LEAGUES` (nba,nfl,ncaaf,mlb), `ESPN_POLL_SECONDS`
- `USE_LLM`, `ANTHROPIC_API_KEY`, `LLM_MODEL`
- `STREAM_URL`, `BUFFER_DIR`, `CLIPS_DIR`, `PRE_ROLL_SECONDS`, `POST_ROLL_SECONDS`
- `DATABASE_PATH`, `ENABLE_S3`, `S3_BUCKET`, `S3_PREFIX`

## other stuff

- `python -m bigplays.main ppv list|live|resolve|record` - helpers for finding an HLS url off ppv.to. dont hammer their api
- `python -m bigplays.main assemble reels` - dumps all current clips into one reel.mp4
- `pytest -q` for the tests, `ruff check .` if you care

## known jank

- espn's public endpoints arent official, they change whenever. fine for a project, get a real feed for anything serious
- the old scoreboard-only agent (`agent run`) only sees score deltas as "events". the live agent uses espn's gamecast play-by-play instead
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
authentication with Node kept private. The legacy `agent run` CLI predates
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
