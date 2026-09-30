# Live streams, college football and highlights

BigPlays uses React/Vite, FastAPI, a private Node resolver, and an FFmpeg rolling
buffer. FastAPI serves the built dashboard and proxies HLS at the same origin.

## Current status — September 26, 2026

- Working provider: `https://ppv.st`, API `https://api.ppv.st/api`. The site's own
  runtime configuration identifies this API. The former `ppv.to` domain serves a
  seizure page and its API fails TLS; old event URLs remain accepted as aliases.
- NBA, NFL and college football (FBS, including unranked teams) are discovered
  independently through ESPN. The September 26 daytime check found **16 live
  CFB games and five unique PPV matches**, with no source warnings. Earlier
  overnight checks had found upcoming games but no published CFB listings yet.
  Discovery/matching now repeats every minute in the persistent background agent, even with the dashboard closed. Listings
  are never fabricated or substituted with a different game/channel.
- Real CFB acceptance tests used the dashboard's **Watch & record** buttons for
  **Notre Dame at Purdue** (ESPN `401858467`), **Texas at Tennessee**
  (`401856704`), and **Illinois at Ohio State** (`401858465`). All passed Chrome HLS playback, FFmpeg recording, the **Cut
  last 14 seconds** action, CFB/game-ID sidecar checks, and automatic saved-clip
  playback through the dashboard event feed. The first two captures showed
  broadcast breaks/advertisements. Illinois–Ohio State showed the actual
  teams/game on screen, and the saved highlight showed football footage.
- Duration checks exposed short cuts when burst arrivals and discontinuities
  made wall-clock segment timestamps differ from media duration. Dashboard
  recordings now pace FFmpeg at playback speed, and manual cuts accumulate
  measured video duration from the newest completed segments in this recording;
  the browser acceptance test also checks that a requested 14-second cut is
  between 10 and 20 seconds, allowing for segment/keyframe boundaries.
- The original resolver handshake/WASM successfully resolved the provider's
  NFL Network football feed. Chrome HLS playback passed. FastAPI started FFmpeg,
  recorded the feed, cut a highlight, served the MP4, and FFmpeg decoded it.
  Chrome subsequently played that saved highlight successfully.
  This overnight check preceded the daytime CFB tests above.
- 53 Python tests and eight Node tests passed. The frontend production build
  passed (existing large-bundle warning). CFB tests cover school-name matching,
  ambiguous mascots, upcoming listings and recording/clip metadata.

A validation clip is available in Incoming plays as **Live stream validation**:
`data/clips/e9012ba9f4fc4566b64762ce62517f43.mp4`. Test artifacts are ignored by Git.
Safari native HLS and playback from another device remain untested.

Final live-CFB validation at **11:28 a.m. PDT, September 26** produced
`data/clips/6d539ed8495f49e08e1813ba953c6cdf.mp4`: **14.01 seconds** from the
Illinois–Ohio State feed (a commercial break at capture), `league: ncaaf`,
ESPN game `401858465`. An earlier four-second validation cut,
`data/clips/6597e00c3f314923a8e1f7e6dff9668a.mp4`, shows actual game action. Chrome
played the saved clip, FFmpeg decoded it, and FastAPI served it successfully.
That manual test recording was stopped. The app, resolver and persistent agent
now remain running.

## Timestamped plays and continuous monitoring

Automatic clips now require game-thread reactions and local-model approval before
cutting. Public-browser collection is enabled, but Reddit currently blocks it with
human verification. The gate therefore holds automatic clips while recording
continues; explicit manual cuts remain available. See [reaction gate setup and
validation](reddit-setup.md). The earlier clip validations below predate this gate.

The separate `agent` process in `process-compose.yaml` runs continuously while
the computer and supervisor are running, without a browser or Codex turn. It
discovers games every 60 seconds, polls each selected game's ESPN play-by-play
every five seconds, and archives original signed-relay MPEG-TS segments every
two seconds. It records two matched games by default, prefers CFB, retains its
selection on restart, and moves on after a game ends (with a three-minute delay
for the broadcast). Process Compose restarts it on failure. It cannot operate
while the computer is asleep or shut down; unattended hosting requires an
always-on machine.

The archive uses `EXT-X-PROGRAM-DATE-TIME` plus segment durations, not download
time or filenames. This is the absolute-time association specified by
[HLS RFC 8216 §4.3.2.6](https://www.rfc-editor.org/rfc/rfc8216#section-4.3.2.6).
Media-sequence deduplication tolerates the provider's millisecond rounding when
sliding playlists re-anchor their timestamps. Missing segments fail the cut;
the cutter never substitutes the current live edge. Cuts are re-encoded to the
selected interval for accurate boundaries.

ESPN `wallclock`, quarter and game clock identify the desired play. Local OCR
matches both team labels and the quarter/clock in archived video to account for
broadcast delay. Scoring plays (excluding football PATs), turnovers and football
gains of 20+ yards queue for fan-reaction review automatically. **Cut manually ·
skip hype check** explicitly bypasses social approval for a chosen play.
The default window is 12 seconds before the matched on-screen clock and four
seconds after it. Clips have stable game/play IDs, so restarts do not duplicate
them. Originals are retained up to 30 minutes or 2 GiB per game; inactive
archives also expire. Saved highlights are retained separately.

An unreadable clock, unsupported score-bug layout, missing timestamp, encrypted
or fragmented-MP4 archive input, incomplete window or pre-recording play remains
unavailable/pending; no guessed delay is used. Clock matching is a conservative
heuristic, not a guarantee for every broadcast layout or replay. The included
OCR helper uses Apple's Vision locally and builds automatically on macOS with
Swift installed. A non-macOS deployment needs a compatible `SCOREBOARD_OCR_BIN`
executable returning the same text/coordinate JSON format.

Verified against **Houston at Georgia Southern**, play `401856806275`:
Mekhi Hughes' eight-yard run, second quarter **8:33 → 8:28**. ESPN's UTC timestamp
was `21:05:50Z`; matching video appeared near `21:06:50.737Z` (about **61 seconds
later**). The resulting **16.02-second** clip includes the snap, run and tackle,
visually checked at multiple timestamps and played in Chrome:
`data/clips/03cc898914536377769e7c95.mp4`.
This validation explicitly queued an ordinary play through the same agent cutter;
it is separate from the earlier commercial-break test clips.

The dashboard shows heartbeat, active recordings, buffer range, last play check,
alignment status, and pause/resume controls. APIs: `GET /api/agent`,
`POST /api/agent/control {"enabled": false}`, and
`POST /api/agent/clip {"game_id": "…", "play_id": "…"}`. Archive indexes,
play feeds and clock observations persist under `data/agent`; signed relay
URLs remain in memory.

## Run locally

This checkout's tracked `.venv` references an unavailable Python 3.12. The ignored
`.venv-local` was prepared with Python 3.14; Node 22+ is required.

```bash
python3 -m venv .venv-local
source .venv-local/bin/activate
python -m pip install -r requirements.txt
npm ci --prefix frontend
npm ci --prefix ppv-hls-stream-resolver
npm run build --prefix frontend
```

An ignored, mode-0600 `.env` was created for this local checkout with a random
shared resolver secret and exact embed/CDN hosts observed during live validation.
No previous `.env` existed or was overwritten. For a fresh checkout, copy
`env.example` only if `.env` does not exist and generate a secret using
`openssl rand -hex 32`.

| Variable | Value / purpose |
| --- | --- |
| `RESOLVER_BASE_URL` | `http://127.0.0.1:3000`; backend-only Node URL |
| `RESOLVER_API_KEY` | Same random secret of at least 32 characters in Node and FastAPI |
| `RESOLVER_EMBED_HOSTS` | Exact approved embed hosts; observed `embedindia.st` |
| `RESOLVER_MEDIA_HOSTS` | Exact approved playlist/segment/key hosts, comma-separated |
| `LEAGUES` | `["nba","nfl","ncaaf"]`; also enables CFB in the legacy scoreboard agent |
| `BUFFER_DIR`, `CLIPS_DIR` | Persistent writable media paths; default `data/buffer`, `data/clips` |
| `AGENT_DIR` | Agent state and timestamped archives; default `data/agent` |
| `AGENT_MAX_GAMES` | Concurrent automatic game archives; default `2`, maximum `8` |
| `AGENT_BUFFER_MINUTES`, `AGENT_BUFFER_MAX_MB` | Per-game archive caps: `30` minutes / `2048` MiB |
| `AGENT_PRE_ROLL_SECONDS`, `AGENT_POST_ROLL_SECONDS` | Play window around verified video clock: `12` / `4` |
| `SCOREBOARD_OCR_BIN` | Local OCR executable; default `data/tools/scoreboard-ocr` |

Observed media hosts configured locally: `vishnu.indianservers.st`,
`shiva.indianservers.st`, `netanyahu.indianservers.st`,
`p16-common-sign.tiktokcdn-eu.com`, `p19-common-sign.tiktokcdn-eu.com`.
These are exact entries, not wildcard permissions. Future CDN changes must be
reviewed and added explicitly; restart Node after changing its environment.
The committed `env.example` keeps host allowlists empty (deny by default).

The built dashboard and resolver are managed locally with `process-compose.yaml`:

```bash
# Start once; the services have already been started for this checkout.
process-compose -f process-compose.yaml -U -u /tmp/bigplays-process-compose.sock up -D
# Inspect / stop
process-compose -U -u /tmp/bigplays-process-compose.sock process list
process-compose -U -u /tmp/bigplays-process-compose.sock down
```

Open **http://127.0.0.1:8000**. Both services bind loopback. Alternatively, run
these in separate terminals (do not also run the managed services on the same ports):

```bash
node --env-file=.env ppv-hls-stream-resolver/src/server.js
```

```bash
source .venv-local/bin/activate
python -m bigplays.main server run --host 127.0.0.1 --port 8000
```

For frontend development, `npm run dev --prefix frontend` serves port 5173 and
proxies `/api` and `/clips` to FastAPI.

## Behavior and limitations

The panel shows current games and an expandable upcoming schedule. Matching
requires both teams and a unique listing. CFB uses school names/abbreviations,
not shared mascots such as Bulldogs. Explicit league tags and scheduled start
times help reject unrelated listings. Nested event paths such as
`ncaaf/2026-09-26/tex-ten` are supported; traversal, queries, credentials and
unapproved event hosts are rejected.

A PPV failure no longer hides successful ESPN results. A failed scoreboard does
not hide another league. The UI distinguishes source outages, unmatched games,
ambiguous listings and no games in progress.

Click **Watch & record** when a listing is available, allow at least 20 seconds
(longer for long GOPs), then **Manual cut: latest 14 seconds**. These manual cuts are approximate because
FFmpeg copies whole segments. Recording uses native playback speed to avoid
overwriting timestamped segments during burst downloads. Manual cuts select by
measured media duration, and wait for enough completed footage; network delays
and timestamp discontinuities no longer shorten the selection window.
Clips retain the selected ESPN game ID, league and
name. Manual URLs without game context use a unique manual ID and `unknown`
league, visible under All. The CFB highlight filter uses the `ncaaf` league.

The manual recorder supports one game independently of the background agent's
separate per-game archives. Use the background monitor for automatic selection,
switching and timestamp-aligned plays. Do not run the old scoreboard-only CLI
against these archives; its polling-time alignment is obsolete. The legacy CLI
`ppv resolve` / `ppv record` still uses its old HTML resolver; use the dashboard
for the integrated Node path.

## Deployment and relay

Deploy FastAPI and Node as persistent services. Expose FastAPI through
**authenticated HTTPS**, including `/api`, `/api/hls`, and `/clips`; keep Node
private. FastAPI itself does not implement login. Use one FastAPI worker for
recording ownership. In containers use `HOST=0.0.0.0` for private Node and
`RESOLVER_BASE_URL=http://resolver:3000`, with no public resolver port.

Browser URLs and all nested HLS URIs are relative `/api/hls?...` paths, so remote
browsers do not need to reach localhost. Six-hour HMAC signatures bind the media
URL, embed context and expiration. Host allowlists, DNS address checks and
redirect rejection constrain upstream access. DNS validation is not connection
pinned: use egress rules blocking private networks as an additional defense.

The original upstream code supplies the protobuf/WASM handshake and MPEG-TS
wrapper removal. The relay also handles WebP-wrapped segments from the current
provider, binary keys, fragmented MP4, URI attributes, key rotation,
discontinuities and finite byte ranges. FFmpeg's `bytes=0-` probes receive the
complete rewritten playlist or unwrapped segment; original byte offsets cannot
be applied to transformed bodies.

Resolution runs in isolated workers: 45-second total timeout, maximum two workers.
Upstream fetches have 15-second limits, DNS checks five seconds; relay concurrency
is limited to 32. Metadata/handshake bodies are capped at 2 MiB, media at 32 MiB.
FastAPI timeouts are 50 seconds for resolution and 25 for relay. Re-resolve after
six-hour expiry. The panel reports FFmpeg exits.

The frontend dependency audit reports existing Vite/esbuild development-tool
advisories. Production uses the built frontend through FastAPI; a toolchain
upgrade remains separate work.

## Repeat validation

```bash
.venv-local/bin/python -m pytest -q
node --test ppv-hls-stream-resolver/test/*.test.js
npm run build --prefix frontend
# Real network and recording test, using an authorized event:
.venv-local/bin/python scripts/validate_live_stream.py --url https://ppv.st/live/nfl-network --record
# Installed Chrome; services running. Confirms CFB discovery and video advancement:
npm test --prefix frontend
# Persistent monitor + a saved aligned play; verifies browser playback and heartbeat:
npm run test:agent --prefix frontend
```

The browser smoke test defaults to the same football feed. Set
`BIGPLAYS_TEST_EVENT` and `BIGPLAYS_TEST_BASE` to use another authorized event or
app origin. It checks saved-highlight playback when a validation clip exists.
Screenshot: `data/validation/dashboard.png`. Provider catalog availability does
not guarantee every individual scheduled event has begun broadcasting.

To repeat the full live-CFB browser test without entering an event URL:

```bash
npm run test:cfb --prefix frontend
# Optionally choose a currently matched ESPN game and observe through an ad break:
BIGPLAYS_TEST_GAME_ID=401856704 BIGPLAYS_TEST_OBSERVE_SECONDS=60 npm run test:cfb --prefix frontend
```

The test discovers a live match, clicks its dashboard button, verifies playback
advancement, cuts a clip, checks `ncaaf` and the selected game ID, and confirms
that exact clip plays in the dashboard. It refuses to interrupt an existing
recording and stops its own recording on completion. Results and screenshots:
`data/validation/cfb-result.json`, `cfb-live.png`, `cfb-highlight.png`.
Clips are approximate segment cuts, not guaranteed to be exactly 14 seconds.

## Source delivery and changed files

`ppv-hls-stream-resolver/` is vendored as ordinary source files. The resumed
checkout contained only an empty Git link; the pinned upstream commit
`1afba44c7e71fa4d6672e1afd9a0ff713a0681eb` was restored and hardened. Its original
WASM/glue files match upstream byte-for-byte. The earlier nested README edits
were absent from this checkout. See `ppv-hls-stream-resolver/UPSTREAM.md`.

- `bigplays/ingest/live_streams.py`, `ppv_provider.py`, `espn.py`: discovery,
  upcoming matches, verified provider endpoints and college football.
- `bigplays/orchestrator/live_agent.py`, `ingest/plays.py`, `media/timeline.py`,
  `media/scoreboard.py`, `scripts/scoreboard_ocr.swift`: continuous monitoring,
  timestamped archives, local clock alignment and historical play cuts.
- `bigplays/server/agent.py`, `frontend/src/components/AgentMonitor.tsx`:
  heartbeat, pause/resume, archive status and play-specific clip requests.
- `tests/test_play_timeline.py`, `tests/test_live_agent.py`, `tests/test_agent_api.py`,
  `frontend/tests/agent-smoke.mjs`: historical-window, deduplication, timestamp,
  automatic clipping, restart, API and real browser validation.
- `bigplays/models.py`, `config.py`, `main.py`: `ncaaf` support.
- `bigplays/server/streams.py`, `bigplays/media/stream_buffer.py`,
  `bigplays/media/hls_clipper.py`: gateway, recording metadata/lifecycle,
  relay ranges, FFmpeg cleanup and cuts based on actual media duration.
- `frontend/src/components/LiveStream.tsx`, `TopBar.tsx`, `App.tsx`, `types.ts`,
  `styles.css`: CFB controls, upcoming schedule, matching status and playback.
- `ppv-hls-stream-resolver/`: original resolver plus private-service hardening,
  updated endpoint, nested event paths, HLS compatibility and Node tests.
- `tests/test_live_streams.py`, `tests/test_stream_gateway.py`, `tests/test_recent_clip.py`,
  `scripts/validate_live_stream.py`, `frontend/tests/stream-smoke.mjs`,
  `frontend/tests/live-cfb.mjs`: regression
  and real-stream validation.
- `frontend/package.json`, lockfile: browser test dependency/command.
- `env.example`, `.gitignore`, `README.md`, this guide, `process-compose.yaml`:
  configuration, reproducible local services and handoff. No commits were made.
