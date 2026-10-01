# BigPlays: agent instructions

Read [CLAUDE.md](CLAUDE.md); it applies to every coding agent. In short:

- **Start locally with `scripts/dev.sh`.** Add `--agent` for live capture and
  `--expo` for the Expo app on :8082.
- **Python env is `.venv-local`,** never `.venv`.
- **Rebuild the web app after pulling** (`npm run build --prefix frontend`). The
  server serves `frontend/dist`.
- **Don't use `--demo`, `demo run`, `agent run`, or `DEMO_MODE=true`.** Those are
  retired.
