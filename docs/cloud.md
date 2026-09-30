# Always-on cloud monitor

`compose.cloud.yaml` runs the current agent, FastAPI dashboard and private Node
resolver on a Linux Docker host. It checks for games every minute and polls plays
every five seconds. It records up to `AGENT_MAX_GAMES` matched games (two by
default), including NBA, NFL and college football. A laptop or open browser is
not required once this stack is running on a cloud server.

This repository contains deployment files, not a provisioned cloud server.
Choose a provider/server and budget before purchasing hosting. CPU, memory,
storage and bandwidth must be measured with actual concurrent games; local Qwen
inference adds memory and CPU demand. The stream resolver and Reddit must also
be reachable from the chosen datacenter.

## Prepare the server

Install Docker Engine and the Compose plugin on a persistent Linux VM and enable
the Docker service at boot. Copy this working source tree (including uncommitted
agent files) to the server; do not copy local `.env`, virtualenvs, `node_modules`
or existing `data` as part of the image. Building from the GitHub default branch
alone will not include uncommitted work.

```sh
cp cloud.env.example cloud.env
chmod 600 cloud.env
```

Fill `cloud.env` with a random resolver key of at least 32 characters and the
approved embed/media host lists from the working setup. Use `openssl rand -hex 32`
to generate a new key. Compose requires all three values; the resolver rejects
short keys. Keep the file private. Always include `--env-file cloud.env` in
Compose commands so resolver and Python receive the same key.

The cloud image includes FFmpeg, Tesseract English OCR and Playwright Chromium.
Tesseract replaces the macOS Vision executable and emits the same normalized
scoreboard coordinates. Missing or uncertain clock matches continue to hold
clips; no estimated delay is used instead. Linux OCR accuracy needs verification
against each broadcaster's score graphic.

## Preserve fan-reaction review

The default keeps `SOCIAL_CLIP_GATE=true`. Configure approved Reddit API access
using `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, the identifying user agent and
any required refresh token. Browser collection is available with
`REDDIT_SOURCE=browser`, but the existing local browser was blocked by Reddit;
moving to a cloud IP does not guarantee access. No verification challenges are
bypassed. Missing/blocked social data holds automatic clips while recording
continues.

For the existing local Qwen model, start Ollama and download the model once:

```sh
docker compose --env-file cloud.env -f compose.cloud.yaml --profile local-llm up -d ollama
docker compose --env-file cloud.env -f compose.cloud.yaml --profile local-llm exec ollama ollama pull qwen3:4b
docker compose --env-file cloud.env -f compose.cloud.yaml --profile local-llm up -d --build --wait --wait-timeout 180
```

The model cache persists in its own volume. CPU inference has not been
benchmarked on your server. If you explicitly choose Anthropic instead, configure
`SOCIAL_LLM_PROVIDER=anthropic`, `SOCIAL_LLM_MODEL` and `ANTHROPIC_API_KEY`, omit
the Ollama steps and profile, and budget separately for API usage.

If you explicitly choose to skip fan-reaction review, set
`SOCIAL_CLIP_GATE=false`; initial sports filters still select plays and exact
clock alignment remains mandatory. Ollama and Reddit are then unnecessary for
clipping. This changes the selection policy and is not the default.

## Connect and verify

The dashboard binds only to server loopback. Resolver and Ollama have no
published ports. From your laptop:

```sh
ssh -N -L 8000:127.0.0.1:8000 your-user@your-server
```

Open `http://127.0.0.1:8000`. To expose a public URL later, put authenticated
HTTPS in front of all routes, including API, HLS and clips. The app has no login.

```sh
docker compose --env-file cloud.env -f compose.cloud.yaml ps
docker compose --env-file cloud.env -f compose.cloud.yaml logs --tail 100 agent
curl --fail http://127.0.0.1:8000/api/agent
```

Check `running`, a recent `heartbeat`, `enabled`, discovery errors and Reddit
status. During a live matched game, verify increasing segments, clock
observations and new clips. A healthy process alone does not prove successful
stream access, social review or clipping. The final acceptance test is a real
new play producing a playable, correctly aligned clip on this cloud host.

## Restart, persistence and capacity

Docker's [restart policy](https://docs.docker.com/reference/compose-file/services/#restart)
restarts exited services and starts them after host reboot unless manually
stopped. Health checks expose stale heartbeats and failed services; Compose does
not restart a still-running process solely because it is unhealthy. Connect
health/error monitoring to your hosting provider before relying on unattended
operation. Discovery and stream requests retry temporary failures internally.

Recordings, clips, control state and review decisions share a named `footage`
volume, so restarts retain state and clip IDs prevent duplicate cuts. Only run
one agent replica on this volume; its lock prevents concurrent writers.
The agent may remain paused across restarts if paused from the dashboard.

Per-game archives are bounded by buffer duration and bytes. Saved highlights
are retained indefinitely, so monitor free disk space and back up the volume.
There is currently no automatic cloud-agent upload to S3; the legacy `ENABLE_S3`
setting does not enable uploads in this newer live agent. Outbound playback
traffic can add hosting charges. Container logs rotate at 10 MB, three files.

To deploy configuration or code changes, repeat `up -d --build --wait` (include
the profile if using Ollama). To stop, use `down` with that profile. Do not use
`down -v` unless you intend to delete stored footage and model volumes.

## Validation performed

Both Docker targets built successfully on Linux ARM64. The runtime container
passed all 77 Python tests, including FFmpeg extraction and Tesseract clock
recognition from a synthetic scoreboard video. The resolver container passed
all eight Node tests. Chromium launched successfully in the runtime image.
An isolated runtime smoke test verified the built dashboard, agent heartbeat,
shared control files, pause/resume and graceful shutdown. Compose configuration
validation passed and rejected missing resolver settings as intended.

These checks used no live social or stream services. Cloud provisioning, x86
builds, model performance on the selected host, external access, and a real
game-to-highlight run remain to be verified on the chosen server.
