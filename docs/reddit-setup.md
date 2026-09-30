# Fan reactions before clipping

The background pipeline is now:

1. ESPN's initial filter selects scoring plays, turnovers and big gains.
2. Collect timestamped comments from that game's Reddit thread.
3. Local Qwen3 4B evaluates fan excitement and attribution to the candidate.
4. Only an approved candidate is cut from the rolling archive, using the matched
   on-screen quarter/game clock and original video timestamps.

This applies to every candidate in the games currently being archived (two games
by default), not every simultaneous game on the schedule. Recording and clock
scanning continue while social review waits. Existing clips are preserved.

## Current live status

**The gate is enabled, but live browser collection is blocked.** On September 26,
2026 Reddit returned its human-verification page to headless Chrome. The monitor
reports `browser_access_blocked`, backs off for five minutes across all games,
and holds automatic clips. It does not classify missing data as weak hype or
silently cut anyway. X is not connected.

A prior manual public-web snapshot could be read through the assistant's web
access. That interface is not an unattended service available to the background
Python process. Its relative ages and partial coverage are insufficient to
approve individual plays. No old snapshot is recycled as fresh reactions.

The browser adapter reads visible DOM only, up to 100 comments per page, retaining
only comments with exact timestamps. It never solves verification challenges,
automates login, spoofs fingerprints, or substitutes a scrape from another host.
The DOM extractor has fixture coverage but cannot yet be verified against a
readable live Reddit page in this environment.

## Local setup

Ollama is installed and running privately on `127.0.0.1:11434`; `qwen3:4b` is
installed (about 2.5 GB on disk). No LLM API key or per-request charge is required.
For a fresh machine:

```bash
brew install ollama
brew services start ollama
ollama pull qwen3:4b
npm ci --prefix frontend
```

Browser mode uses installed Google Chrome and Playwright from the frontend's dev
dependencies. Keep those dependencies installed on the local monitor machine.
Set these in the existing ignored `.env`, preserving its resolver settings:

```dotenv
SOCIAL_CLIP_GATE=true
REDDIT_SOURCE=browser
REDDIT_ENABLED=true
REDDIT_POLL_SECONDS=30
USE_LLM=true
SOCIAL_LLM_PROVIDER=ollama
SOCIAL_LLM_MODEL=qwen3:4b
OLLAMA_BASE_URL=http://127.0.0.1:11434
SOCIAL_LLM_CALLS_PER_HOUR=200
```

The current local settings use these values. The shared hourly cap limits local
work; reaching it holds candidates and retries later within their review window.
It never becomes implicit approval. Inference is serialized across games.

```bash
process-compose -U -u /tmp/bigplays-process-compose.sock process restart agent
process-compose -U -u /tmp/bigplays-process-compose.sock process restart app
```

The supervisor reloads `.env` on each app/agent restart.

## Review rules

- Match both team names and the game's date; exclude postgame threads. Follow the
  latest matching split thread and cache discovery for five minutes.
- Review after 90 seconds to allow fan reactions to arrive. An unapproved candidate
  gets at most one further model attempt after four minutes, at least 90 seconds
  after its first attempt. Stop reviewing after ten minutes. Finished games stay
  monitored for eleven minutes to allow final-play review and clipping.
- Use comments between 15 seconds before and four minutes after the play's ESPN
  time, with nearby play descriptions supplied to the model to flag ambiguity.
  Different viewers have different delays; generic hype alone may be insufficient
  to establish which play people mean.
- Keep one comment per author in a reaction window. The same OMG from several
  distinct people counts; one person's repeated messages do not. Count hype
  phrases/emojis and a 15-second burst of distinct commenters. Compare activity
  against a baseline only if enough preceding history was actually observed.
- Sample at most 20 comments, each capped at 800 characters. Raw text and author
  hashes stay in RAM for up to 15 minutes, capped at 5,000 comments per game.
- Require a spectacular or clutch play judgment, hype score at least **0.75**,
  attribution confidence at least **0.70**, at least **three distinct commenters**
  and at least **two valid cited comment IDs**. Referee complaints, routine plays,
  injuries, unrelated chatter and ambiguous attribution do not pass.
- Persist decisions and attempts in each game's `social-reviews.json`, without
  raw comments. Restarts do not repeat completed reviews. ESPN corrections change
  the play fingerprint and invalidate old approval. Observed deleted evidence
  revokes pending approval and clears endorsements on existing clips.
- Approved clips carry the assessment, source comment links and a score combining
  70% initial play score and 30% model hype. “Fan-backed” means local evidence of
  highlight potential; it is not proof of broad virality.
- **Cut manually · skip hype check** is an explicit manual override. It still
  requires the correct historical video window. `SOCIAL_CLIP_GATE=false` restores
  automatic cutting from the initial sports filter; this checkout leaves it true.

## Optional API collection

For approved Reddit API access, change `REDDIT_SOURCE=api` and configure
`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, an identifying `REDDIT_USER_AGENT`, and
optionally `REDDIT_REFRESH_TOKEN` for the approved OAuth flow. The existing OAuth
collector and rate-limit backoff remain available.

The [prepared request](reddit-access-request.md) has not been submitted. Reddit's
[Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
requires approval for API access. Its [request form](https://support.reddithelp.com/hc/en-us/requests/new)
was opened in the user's browser previously.

Anthropic remains opt-in through `SOCIAL_LLM_PROVIDER=anthropic`, a compatible
`SOCIAL_LLM_MODEL`, and `ANTHROPIC_API_KEY`. This sends bounded samples to a cloud
provider; the request draft describes local inference instead. There is no
automatic cloud fallback if Ollama fails.

X has official [recent-post search](https://docs.x.com/x-api/posts/search/introduction)
and [paid API access](https://docs.x.com/x-api/getting-started/pricing). It is not
implemented in this collector.

## Validation

```bash
.venv-local/bin/python -m pytest -q
node frontend/tests/reddit-reader.mjs
npm run build --prefix frontend
.venv-local/bin/python -m scripts.validate_local_llm
```

**74 Python tests passed.** Coverage includes pre-cut approval, blocked/failed/insufficient reviews,
repeated hype versus single-author spam, delayed rechecks, persisted decisions,
play corrections, deleted evidence, approval revoked during encoding, exact
historical cuts, and manual overrides.
Browser DOM fixtures verify exact timestamps and separation of nested comments.
The frontend build has its existing bundle-size warning.

Actual local inference passed synthetic excitement and officiating cases after
the gate change (roughly 2.5s and 1.4s on this Mac). These tests establish wiring
and two simple judgments, not broad model accuracy. **A live Reddit → decision →
clip end-to-end run remains unverified because browser access is blocked.**
