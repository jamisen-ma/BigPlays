#!/usr/bin/env bash
# Start BigPlays locally from the current code, so you never see a stale UI.
#
#   scripts/dev.sh              API + web app on http://127.0.0.1:8000
#   scripts/dev.sh --agent      ...plus the live capture agent
#   scripts/dev.sh --expo       ...plus the Expo app in a browser on http://localhost:8082
#   scripts/dev.sh --agent --expo
#
# Uses .venv-local (never the old committed .venv). Ctrl+C stops everything it started.
set -euo pipefail
cd "$(dirname "$0")/.."

AGENT=0; EXPO=0
for arg in "$@"; do
  case "$arg" in
    --agent) AGENT=1 ;;
    --expo) EXPO=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

PY=.venv-local/bin/python
if [ ! -x "$PY" ]; then
  echo "creating .venv-local…"
  python3 -m venv .venv-local
fi
"$PY" -m pip install -q -r requirements.txt
[ -f .env ] || { cp env.example .env; echo "created .env from env.example"; }

echo "building the web app from current source…"
[ -d frontend/node_modules ] || npm ci --prefix frontend --no-audit --no-fund
npm run build --prefix frontend

pids=()
cleanup() { for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if [ "$AGENT" = 1 ]; then
  "$PY" -m dotenv -f .env run --override -- "$PY" -m bigplays.orchestrator.live_agent &
  pids+=($!)
fi
if [ "$EXPO" = 1 ]; then
  [ -d mobile/node_modules ] || npm ci --prefix mobile --no-audit --no-fund
  (cd mobile && EXPO_PUBLIC_API_URL=http://127.0.0.1:8000 npx expo start --web --port 8082) &
  pids+=($!)
fi

echo "BigPlays: http://127.0.0.1:8000"
"$PY" -m dotenv -f .env run --override -- "$PY" -m bigplays.main server run --host 127.0.0.1 --port 8000
