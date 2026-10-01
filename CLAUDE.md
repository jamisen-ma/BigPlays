# BigPlays: notes for AI coding agents

## Running it locally

Use the start script. It builds everything from the current source:

```bash
scripts/dev.sh              # API + web app → http://127.0.0.1:8000
scripts/dev.sh --agent      # also run the live capture agent
scripts/dev.sh --expo       # also run the Expo app in a browser → http://localhost:8082
```

If you start things by hand instead:

1. **Python env is `.venv-local`.** Never use `.venv`: it's an old environment from
   an earlier commit and isn't maintained.
   `python3 -m venv .venv-local && .venv-local/bin/pip install -r requirements.txt`
2. **Always rebuild the web app after pulling.** The server serves `frontend/dist`,
   so an old build shows an old UI:
   `npm ci --prefix frontend && npm run build --prefix frontend`.
   (`server run` also rebuilds automatically when `frontend/src` is newer than the
   build. Set `FRONTEND_AUTO_BUILD=false` to skip that.)
3. **Server:** `.venv-local/bin/python -m bigplays.main server run --host 127.0.0.1 --port 8000`
4. **Live agent (optional):** `.venv-local/bin/python -m dotenv -f .env run --override -- .venv-local/bin/python -m bigplays.orchestrator.live_agent`
5. **Expo app (optional):** `cd mobile && npm ci && EXPO_PUBLIC_API_URL=http://127.0.0.1:8000 npx expo start --web --port 8082`

## Don't

- **Don't run the old commands:** no `--demo` / `demo run` (demo mode is retired),
  and no `bigplays.main agent run` (the legacy agent). The live agent is
  `bigplays.orchestrator.live_agent`.
- **Don't set `DEMO_MODE=true`.** `.env` should have `DEMO_MODE=false` or no
  `DEMO_*` lines at all.
- **Don't commit `data/`, `.env`, or `ppv-hls-stream-resolver/` source.**

## Layout

- `bigplays/`: the Python backend
  - `server/`: the FastAPI app, games and social APIs
  - `orchestrator/`: the live agent and hype gate
  - `ingest/`: ESPN gamecast, MLB highlights, Reddit RSS
  - `media/`: buffer, clipper, scorebug OCR
  - `storage/`: SQLite catalog
- `frontend/`: the React + Vite web app. Tests are in `frontend/tests/*.mjs`.
- `mobile/`: the Expo (SDK 57) iPhone app.
- `docs/games-api-contract.md`: the API shapes.

## Tests

```bash
.venv-local/bin/python -m pytest -q
npm run build --prefix frontend && node frontend/tests/games.mjs && node frontend/tests/video-frame.mjs
```
