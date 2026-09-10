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

python 3.10+, ffmpeg on your PATH if you want real clipping (demo mode doesnt need it).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env   # put your ANTHROPIC_API_KEY in there, S3 stuff is optional
```

then in separate terminals:

```bash
# records the broadcast into a rolling buffer
python -m bigplays.main recorder start --stream-url "$STREAM_URL"

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
```

the plays are from the 2025-26 nba season and the 2025 nfl season - anunoby's tip in to finish the 29 pt comeback in finals game 4, brunson's floater in the clincher, booker over caruso w/ 0.7 left, KD's revenge 3 on phoenix, reaves' buzzer beater in minnesota, hachimura's corner 3 off lebron, wemby's 12 block game, the jamal cain poster on duren, caleb -> dj moore walk off vs green bay, treylon burks' one hander, shaheed's 95 yd kick return, caleb's 4th down miracle to kmet, nwosu's super bowl pick six. list is in `bigplays/demo/plays.py`, add your own if you want.

couple notes on how the footage works:
- it streams thru youtube's embed player under the hood, nothing gets downloaded (dont wanna deal w/ youtube tos or the leagues lawyers). the player hides all the youtube ui, crops the title bar out and draws its own controls so it looks like the agent cut it. each play has a start/end so you only see the ~10 sec of the actual play, no replays or interviews
- nba's channel lets you embed, nfl's doesnt (error 150), so nfl plays come from fox / nbc / fan uploads. if an owner blocks one later it falls back to a synthetic rendered clip automatically
- to check if a new video id actually embeds theres `frontend/dev/embedtest.html`, copy it into public/ and open it. oembed lies about this so dont trust it
- theres also a synthetic clip renderer (`bigplays/demo/clip_gen.py`, pillow + the ffmpeg that comes with imageio-ffmpeg) that draws little animated players/ball/scoreboard. thats the fallback when footage doesnt load. `python -m bigplays.main demo clips` renders them

## api

- `GET /api/highlights` - everything on disk, newest first
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
- `ENABLE_S3`, `S3_BUCKET`, `S3_PREFIX`
- `DEMO_MODE`, `DEMO_CLIPS_DIR`, `DEMO_MIN_INTERVAL`, `DEMO_MAX_INTERVAL`

## other stuff

- `python -m bigplays.main ppv list|live|resolve|record` - helpers for finding an HLS url off ppv.to. dont hammer their api
- `python -m bigplays.main assemble reels` - dumps all current clips into one reel.mp4
- `pytest -q` for the tests, `ruff check .` if you care

## known jank

- espn's public endpoints arent official, they change whenever. fine for a project, get a real feed for anything serious
- the scoreboard api doesnt give play by play so "events" are score deltas. the demo plays have the real descriptions baked in
- clip timing depends on your system clock being right, use ntp
- autoplay is muted (browser rules), hit the speaker icon
- dont expose the dashboard without putting auth in front of it

MIT
