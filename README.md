## BigPlays — Autonomous Sports Highlight Agent (NBA/NFL)

BigPlays is an end-to-end Python project that monitors live NBA and NFL games, reasons about what moments are "viral-worthy" using an LLM (Claude via LangChain), and automatically triggers FFmpeg workflows to clip and assemble highlight reels. Clips are tagged with rich metadata and stored locally and/or in Amazon S3 for downstream distribution.

### Key Features
- Live game monitoring from ESPN public scoreboards (NBA and NFL) via HTTP polling
- Optional social signals ingestion (Reddit stubs and mock social bursts)
- LLM-powered reasoning (Claude) to judge hype/virality, tag play types, players, and generate titles
- Rule-based heuristics blended with LLM scores for robust highlight detection
- Continuous HLS recording to a rolling local buffer using FFmpeg
- Near-real-time clipping around detected events; MP4 outputs with sidecar JSON metadata
- Automatic S3 uploads with object metadata and lifecycle-friendly keying
- Highlight reel assembly at halftime/final whistle or on-demand
- Simple FastAPI dashboard to list highlights and reels
- Typer CLI to run individual services or the full agent

### Architecture Overview
- Ingestion: `ESPNScoreboardPoller` fetches NBA/NFL game states and scoring updates. Optional `RedditStream` can enrich signals. A `MockSocialBurst` generator can be enabled for demos.
- Detection: `HighlightDetector` fuses scoring deltas, lead changes, clutch/time context, and social bursts with an LLM judgment for a final hype score and tags.
- Media: `StreamBuffer` records an HLS stream to timestamped `.ts` segments; `HLSClipper` extracts clips around the event time and remuxes to `.mp4`.
- Storage: `LocalStore` writes outputs under `data/`; `S3Store` uploads with JSON sidecars.
- Assembly: `ReelBuilder` combines highlight clips by game and time windows (halftime/final) with intro/outro bumpers (optional).
- Serving: `FastAPI` app serves a lightweight dashboard and JSON APIs for highlights.
- Control: `Typer` CLI orchestrates processes; environment-driven config via Pydantic.

### Requirements
- Python 3.10+
- FFmpeg installed and on PATH (`ffmpeg` and `ffprobe`)
- Optional: AWS credentials configured for S3 (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION` or use an instance role)
- Optional: Anthropic API key for Claude (`ANTHROPIC_API_KEY`)
- Optional: Reddit API creds if enabling Reddit ingestion

### Quickstart
1) Create a virtual environment and install dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Copy and edit environment
```bash
cp env.example .env
# Edit .env with your values (ANTHROPIC_API_KEY, S3 bucket, etc.)
```

3) Provide an HLS stream URL for the game broadcast (or a channel feed with DVR). You can supply a public HLS URL or your own encoder's HLS endpoint. Put it into `.env` as `STREAM_URL`.
   - Optional: Use PPV.to discovery to resolve an HLS URL automatically from their `/api/streams` and pages. See commands below.

4) Start the rolling recorder in one terminal
```bash
python -m bigplays.main recorder start --stream-url "$STREAM_URL"
```
This writes timestamped `.ts` segments under `data/buffer/segments/` and maintains an index for precise clipping by wall-clock time.

5) In another terminal, start the agent (ingestion + detection + clipping + storage)
```bash
python -m bigplays.main agent run --league nba
```
Use `--league nfl` for NFL, or omit to monitor both.

6) Start the dashboard (optional)
```bash
python -m bigplays.main server run --host 0.0.0.0 --port 8000
```
Open `http://localhost:8000` to view highlights.

### CLI Overview
```bash
python -m bigplays.main --help
python -m bigplays.main agent run --help
python -m bigplays.main recorder start --help
python -m bigplays.main server run --help
python -m bigplays.main assemble reels --help
python -m bigplays.main ppv list --help
python -m bigplays.main ppv live --help
python -m bigplays.main ppv resolve <uri_name>
python -m bigplays.main ppv record <uri_name>
```

### Configuration
Configuration is environment-driven (see `.env.example`). Key variables:
- Ingestion
  - `LEAGUES`: comma-separated list (e.g., `nba,nfl`)
  - `ESPN_POLL_SECONDS`: default 5
- LLM and Detection
  - `USE_LLM`: `true|false` (fallback to heuristics when false)
  - `ANTHROPIC_API_KEY`: Claude API key
  - `LLM_MODEL`: default `claude-3-5-sonnet-20240620`
- Media
  - `STREAM_URL`: HLS URL to record
  - `BUFFER_DIR`: directory for `.ts` segments (default `data/buffer`)
  - `CLIPS_DIR`: directory for finalized clips (default `data/clips`)
  - `PRE_ROLL_SECONDS`: default 8
  - `POST_ROLL_SECONDS`: default 6
- Storage
  - `ENABLE_S3`: `true|false`
  - `S3_BUCKET`: bucket name
  - `S3_PREFIX`: prefix (e.g., `highlights/`)
- Server
  - `SERVER_HOST`, `SERVER_PORT`

### Development and Testing
Run unit tests:
```bash
pytest -q
```

Lint (optional):
```bash
ruff check .
```

### Notes and Limitations
- Public sports APIs can change; ESPN endpoints are used for convenience and may break. Consider integrating a paid feed for production.
- Social ingestion is optional and stubbed for Reddit; you can plug in additional sources (X/Twitter, Discord, etc.).
- For precise clipping, ensure the HLS source has consistent segment durations and that the system clock is accurate (NTP).
- This repository favors modularity; you can replace the LLM reasoner, storage, or ingestion without changing the rest of the system.

### PPV.to integration
- Poll `https://ppv.to/api/streams` to list streams and use `uri_name` or provided `iframe` to discover playback pages.
- The CLI can list categories/streams (`ppv list`), show live items (`ppv live`), resolve an HLS URL (`ppv resolve`), or start recording directly (`ppv record <uri_name>`).
- Respect PPV.to usage guidelines; cache responses and avoid excessive polling.

### Folder Structure
```
bigplays/
  assembly/
  ingest/
  llm/
  media/
  orchestrator/
  server/
  storage/
  utils/
```

### Security
- Do not commit `.env` or credentials. Use AWS IAM roles and least-privilege policies for S3.
- If exposing the dashboard, protect it behind auth.

### License
MIT


