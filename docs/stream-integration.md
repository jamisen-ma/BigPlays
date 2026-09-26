# Live streams and highlights

BigPlays is React/Vite + FastAPI, with local FFmpeg segment storage. No container,
serverless, or cloud deployment configuration was present. FastAPI serves the
built frontend; Vite proxies `/api` during development.

The existing checkout of [ppv-hls-stream-resolver](https://github.com/sharoon7171/ppv-hls-stream-resolver)
at commit `1afba44c7e71fa4d6672e1afd9a0ff713a0681eb` supplies the handshake,
WASM binary/runtime and HLS relay. Local changes harden that service. Its `.git`
directory is nested inside this workspace: preserve these changes in your fork
or vendor the source when deploying; an ordinary parent-repository commit does
not automatically include a nested repository's modified files.

## Local setup

Node 22+ and Python 3.10+ are required. Run from the BigPlays root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm ci --prefix frontend
npm ci --prefix ppv-hls-stream-resolver
cp env.example .env  # only if you do not already have .env
openssl rand -hex 32 # paste output into RESOLVER_API_KEY in .env
```

Set these in `.env`:

| Variable | Value / purpose |
| --- | --- |
| `RESOLVER_BASE_URL` | `http://127.0.0.1:3000`; backend-only private Node service URL |
| `RESOLVER_API_KEY` | At least 32 random characters; same value in Node and FastAPI |
| `RESOLVER_EMBED_HOSTS` | Comma-separated exact approved embed hostnames from provider metadata |
| `RESOLVER_MEDIA_HOSTS` | Comma-separated exact approved playlist, segment and key hostnames |
| `DEMO_MODE` | `false` for live recording |
| `LEAGUES` | `["nba","nfl"]` (Pydantic expects a JSON list) |
| `BUFFER_DIR`, `CLIPS_DIR` | Writable persistent paths, default `data/buffer`, `data/clips` |

Host allowlists are empty by default and deny all; do not add wildcards. A
blocked approved host is identified in the resolver error so it can be reviewed
and added explicitly. Node uses `HOST=127.0.0.1`, `PORT=3000` by default. It loads
the shared environment file using Node's `--env-file` flag:

```bash
# Terminal 1, project root
node --env-file=.env ppv-hls-stream-resolver/src/server.js
# Terminal 2
source .venv/bin/activate
python -m bigplays.main server run
# Terminal 3
npm run dev --prefix frontend
```

Open http://localhost:5173. The Live source panel polls live NBA/NFL scoreboards
and the PPV catalog every minute. A match requires both teams. A unique match
gets **Watch & record**; ambiguous or missing matches never silently choose a
stream. Manual event URLs remain available. There is one active recording at a
time, deliberately matching the app's existing single-buffer architecture.

Click **Watch & record**, allow at least 20 seconds (longer for long source GOPs),
then **Cut last 14 seconds**. The MP4 and metadata appear in Incoming plays.
FFmpeg cuts at segment boundaries, so duration is approximate. These manual cuts
use a generic `live` game ID and `nba` league label; automatic per-game clip
labeling and concurrent per-game buffers are not implemented here. The existing
agent command can still consume the shared buffer, but must be restricted to the
game actually being recorded to avoid mismatched footage.

## Deployment

Build with `npm run build --prefix frontend`. Run FastAPI and Node as persistent
services; route the public HTTPS app origin to FastAPI, including `/api/hls`.
Keep Node on loopback or a private service network. For containers set Node
`HOST=0.0.0.0` and `RESOLVER_BASE_URL=http://resolver:3000`, without publishing port
3000. Use a single FastAPI worker for recording ownership, persistent media
storage, and the same resolver key across Node replicas. Put the dashboard and
all `/api` and `/clips` paths behind your deployment's authentication, as required
by the existing app's deployment model. FastAPI itself does not implement login.

The browser only receives relative `/api/hls?...` URLs. Every nested playlist,
segment and URI attribute is relayed through the same app origin; no public Node
hostname, browser secret, or browser WASM is needed. API resolution requires the
server secret. Relay URLs are HMAC-signed, expire after six hours, and are limited
to exact HTTPS host allowlists. Redirects and private DNS addresses are rejected.
Use network egress rules to block private address ranges as an additional defense
against DNS rebinding between validation and the underlying HTTP client's lookup.

Resolution runs in isolated workers with a 45-second total limit (maximum two
workers); metadata/handshake/media requests have 15-second timeouts. FastAPI uses
50 seconds for resolution and 20 seconds for relaying. Recordings must be
re-resolved after expiry; the panel polls FFmpeg status and reports exits. This
is a persistent-service setup, unsuitable for short-lived serverless functions.

## Validation and current blocker

```bash
.venv/bin/python -m pytest -q
node --test ppv-hls-stream-resolver/test/*.test.js
npm run build --prefix frontend
```

Tests cover URL rejection, exact host restrictions, signed/expired/tampered
relay URLs, nested URI rewriting, the HTTP worker route, backend gateway error
propagation, and unique/ambiguous live-game matching. Gateway tests use fixtures;
they do not establish successful provider playback.

On 2026-09-25, `https://api.ppv.to/api/streams` failed TLS negotiation and
`https://ppv.to/api/streams` returned a domain-seizure HTML page. Consequently no
provider event could be discovered and no real-stream end-to-end test could be
completed. Catalog format support is based on the existing app and resolver;
it remains unverified against a working live catalog.

When the provider is available, the end-to-end test is:

1. Start both services and the UI with approved host allowlists configured.
2. Select a uniquely matched live game using **Watch & record**.
3. Confirm `POST /api/stream` returns `ok: true` and a relative `proxiedUrl`.
4. Confirm the master/media playlist and segment requests use the app origin and
   that video time advances (Chrome/hls.js; separately test Safari native fallback).
5. After the buffer fills, cut 14 seconds and play the resulting highlight.
6. Repeat from a second device on your public deployment to verify no localhost
   dependency; tamper with a relay URL and confirm HTTP 403.

The current stopping point is the implemented and locally tested integration.
Provider playback, real-stream clipping, and browser/device validation remain
blocked or pending; no replacement domains were searched.
