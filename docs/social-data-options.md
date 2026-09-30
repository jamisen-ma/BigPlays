# Social reactions for BigPlays

Verified September 29, 2026. **Mastodon public discussion is working now without a key:** live tests returned 23 NFL and 25 MLB posts after filtering one 40-post page per league. Reddit and X still need approved API credentials. `bigplays/ingest/social_feeds.py` contains the working Mastodon reader and an opt-in X recent-search adapter; neither writes posts or follows accounts.

| Source | Access and cost | Useful for BigPlays |
| --- | --- | --- |
| Reddit Data API | Explicit approval required; eligible free access allows 100 queries/minute per OAuth client, averaged over a 10-minute window. Commercial use needs written approval. | Timestamped comments inside the exact matchup's game thread. |
| X API | Approved developer account, app and Bearer Token; prepaid usage. Current post reads cost $0.005 each ($5 per 1,000 posts), with spending limits available. | Player/team searches with exact time windows and public engagement metrics. |
| Bluesky AppView | Many public reads need no token. The public search request returned HTTP 403 from this app environment. | Possible additional source; access and useful NFL coverage remain unverified here. |
| Mastodon public hashtag timeline | No token on mastodon.social while public previews are enabled; verified HTTP 200. | Current NFL/MLB discussion, including actual Red Sox–Yankees playoff chatter. Partial coverage with possible news and spam. |

Sources: [Reddit access policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy), [Reddit limits](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki), [X search access](https://docs.x.com/x-api/posts/search/introduction), [X pricing](https://docs.x.com/x-api/getting-started/pricing), [Bluesky API reference](https://docs.bsky.app/docs/api/app-bsky-feed-get-author-feed).

## Working public discussion now

Call `await fetch_public_feed('nfl')` or `await fetch_public_feed('mlb')` from `bigplays.ingest.social_feeds`. The result includes `status`, `posts`, `checked_at`, `error`, and `coverage`. Posts retain separate original `created_at` and observed `collected_at`, plain text, source URL, display name, and public metrics. The reader excludes declared bots, reposts, nonpublic and sensitive posts; this cannot identify every bot or off-topic post. Its latest hashtag page is general discussion, not automatically evidence about a particular clip. Cache briefly in memory; the reader does not save raw text. [Official hashtag timeline documentation](https://docs.joinmastodon.org/methods/timelines/#tag)

Try `.venv-local/bin/python -m bigplays.ingest.social_feeds --league mlb`. A blocked provider returns an explicit access status without trying another endpoint or evading authentication.

## Reddit: reuse the existing collector

`bigplays/ingest/reddit.py` already implements OAuth, thread matching, timestamped comment parsing and rate-limit backoff. Configure `REDDIT_SOURCE=api`, `REDDIT_ENABLED=true`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, an identifying `REDDIT_USER_AGENT`, and the approved OAuth flow's refresh token if needed. The prepared [access request](reddit-access-request.md) has not been submitted. Both client credentials are currently absent; browser mode has encountered Reddit verification blocks.

Historical Week 3 collection needs a separate backfill: current discovery searches only the last day, retrieves at most 100 new comments, and the live monitor discards comments older than 15 minutes. Discover September 24–28 game threads with broader search and both teams; filter by their actual UTC creation times. Retrieve the comment tree and expand `more` entries with `/api/morechildren` serially, then filter by play time. Reddit documents search as a post listing, not a global historical comment search; backfill must report incomplete coverage. [Endpoint reference](https://www.reddit.com/dev/api/)

Keep the existing brief in-memory comment retention and source links. Reddit requires removal of deleted content; permanent video storage does not imply permanent retention of social text. [Data handling requirements](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)

## X: recent search now, archive search later

`GET https://api.x.com/2/tweets/search/recent` covers seven days and returns up to 100 posts/request. All Week 3 games are inside that window on September 29. Older backfills use `/2/tweets/search/all`, available to pay-per-use and Enterprise customers, with up to 500 posts/request. Request `created_at,author_id,public_metrics,conversation_id`, use `start_time`/`end_time`, and paginate with `next_token`. [Search documentation](https://docs.x.com/x-api/posts/search/introduction), [full-archive example](https://docs.x.com/x-api/posts/search/quickstart/full-archive-search)

Example query: `("Jalen Hurts" OR "Saquon Barkley") (Eagles OR Bears) lang:en -is:retweet`. Keep replies because they can contain fan reactions. Search around the verified original play time, never the clip's import time. `XRecentSearchClient` now implements this request, timestamp validation, public metrics, source links, rate-limit backoff and explicit access errors. It makes one bounded request (10 posts by default), returns the next cursor without automatically requesting another page, and avoids extra billed user expansions. The implementation is tested with mocked HTTP; no paid live request has been made.

Configure `X_BEARER_TOKEN` and deliberately set `X_PAID_REQUESTS_ENABLED=true` only after setting the provider's spending limit. The adapter stays disabled without that opt-in. It is not scheduled automatically. A manual invocation is `.venv-local/bin/python -m bigplays.ingest.social_feeds --provider x --query 'MLB -is:retweet' --start '<UTC within last 7 days>' --end '<later UTC>' --max-results 10`.

App-only limits are 450 requests/15 minutes for recent search; archive search allows 300/15 minutes and one/second. Respect response reset headers. [Rate limits](https://docs.x.com/x-api/fundamentals/rate-limits)

Set a developer-console spending limit before enabling collection. For example, 10,000 returned posts cost approximately $50 at the current post-read rate, excluding other resources and fees. Billing deduplicates resources within a UTC day, subject to documented exceptions. Query narrowly and cache by post ID. [Pricing and spending controls](https://docs.x.com/x-api/getting-started/pricing)

## Match evidence honestly

Recommended implementation: preserve social creation time and collection time separately; match names, teams, action and the original event's reaction window; deduplicate authors and reposts; attach source URLs and confidence. Current engagement totals are observations collected now, not historical totals at kickoff. Missing coverage should remain unavailable, not receive an invented score. The existing local reaction judge can be reused after normalizing provider records, but its current Reddit-specific source URLs must be generalized first.

The existing Reddit reaction monitor now supports enrichment after a clip is published when `SOCIAL_CLIP_GATE=false`. Social access and inference do not delay clipping; successful later assessments update only assessment/score fields, preserving original video and event timestamps. Deleted evidence or a corrected play removes the old assessment. Public Mastodon discussion and the X adapter are not yet inputs to that Reddit-specific judge.

Bluesky's `app.bsky.feed.searchPosts` supports `since`, `until`, `sort`, and up to 100 results. Its date filters use `sortAt`, which may differ from `createdAt`, and pagination does not guarantee all matches. It therefore also needs explicit timestamp checks and coverage reporting if access becomes available. [Official search schema](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/feed/searchPosts.json)

## Server API (`bigplays/server/social.py`)

- `GET /api/social/feed?league=nfl|mlb&limit=1..100` (default 30): deduplicated Mastodon posts (by post ID and same-author/same-text), newest first, each with `created_at`, `collected_at`, `url`, author and metrics. One upstream request per league per 60 s (longer if the provider sends `Retry-After`); on failure the last good page is served with `stale: true`.
- `GET /api/social/status`: provider connection state only; never fetches. Mastodon is `ok`/`error`/`not_checked`; X is `needs_token`/`disabled_paid`/`ready` and is never called by the API; Reddit is `missing_credentials`/`disabled`/`configured` and only feeds the game-thread judge.
- `GET /api/social/play/{clip_id}`: only posts plausibly about that clip, with match evidence.

Every post has `relevance`: `general_chatter` by default, or `play_evidence` when, within 2 minutes before to 3 hours after the clip's verified `occurred_utc`, it names the clip's player, or names one of its teams together with its action (touchdown, interception, home run, ...). `play_matches` lists the clip ID, matched players/teams/actions, `seconds_after_play` and `confidence` (`high` = player plus team/action within 15 minutes). This is a keyword heuristic, not proof.

Enrichment runs after publication (`SocialEnricher` in `social_ranker.py`): the router's lifespan listens for new-clip `highlight` events and checks the cached feed at 0, 2, 10 and 30 minutes; fresh feed or play-endpoint matches also enrich. Only `social_score` and `social_enrichment` are written to the published sidecar (the clips watcher syncs it into SQLite and emits `highlight_update`). Event times, clip bounds, `combined_score` and the Reddit `social_assessment` are unchanged, and a sidecar is only rewritten when the evidence changes.
