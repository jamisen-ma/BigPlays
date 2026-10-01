# BigPlays: notes for coding agents

## Run it

Use the start script. It sets up `.venv-local`, rebuilds the web app from the
current source, and starts everything:

```bash
scripts/dev.sh              # API + web app → http://127.0.0.1:8000
scripts/dev.sh --agent      # also run the live capture agent
scripts/dev.sh --expo       # also run the Expo app in a browser → http://localhost:8082
```

By hand:

- **Python env is `.venv-local`,** never `.venv` (an old, unmaintained environment):
  `python3 -m venv .venv-local && .venv-local/bin/pip install -r requirements.txt`
- **Rebuild the web app after pulling.** The server serves `frontend/dist`, so an
  old build shows an old UI: `npm ci --prefix frontend && npm run build --prefix frontend`.
  `server run` also rebuilds automatically when `frontend/src` is newer than the
  build (`FRONTEND_AUTO_BUILD=false` skips that).
- **Server:** `.venv-local/bin/python -m bigplays.main server run --host 127.0.0.1 --port 8000`
- **Live agent:** `.venv-local/bin/python -m dotenv -f .env run --override -- .venv-local/bin/python -m bigplays.orchestrator.live_agent`

## Don't

- **Don't use removed commands.** Demo mode (`--demo`, `demo run`, `DEMO_*`
  settings) and the legacy `bigplays.main agent run` are gone. The live agent is
  `bigplays.orchestrator.live_agent`.
- **Don't commit `data/`, `.env`, or `ppv-hls-stream-resolver/` source.**

## Tests

```bash
.venv-local/bin/python -m pytest -q
npm run build --prefix frontend && node frontend/tests/games.mjs && node frontend/tests/video-frame.mjs
```
