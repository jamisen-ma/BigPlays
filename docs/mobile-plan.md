# Mobile app plan (personal, not App Store)

Decided 2026-09-29. Status: in progress. **Update: Expo Go only, no push alerts, no paid Apple account.**

## Decision
- **Personal use only.** No App Store, no public distribution.
- **Server runs on this Mac** (no cloud bill). The phone reaches it over **Tailscale**
  (free, private, works on cellular). Serve with `tailscale serve` for automatic HTTPS.
  Do not expose the server publicly, since it has no auth.
- **Client: Expo (React Native + TypeScript)** in a new `mobile/` folder.
  - Reuse `frontend/src/types.ts`, the API calls, and the `useLiveFeed.ts` logic.
  - Rewrite the screens with RN primitives. Play video with `expo-video`.
- **Install path:**
  1. Expo Go while building ($0).
  2. When push alerts are wanted: paid Apple Developer account ($99/yr), then EAS Build and
     TestFlight with yourself as an internal tester (no App Review).
     Free Apple IDs can't use push, and their builds expire after 7 days.
- **Push:** the server sends alerts through Expo's push service when a big play is clipped.
  Add a small push-token registration + send endpoint to the FastAPI server.

## Mac-hosting caveats
- The Mac must stay on and awake (disable sleep or run under `caffeinate`).
- Clips stream over the home upload connection (~13 MB each).
- Back up `data/` (clips + SQLite) off-machine.
- Bonus: a home IP avoids stream sites blocking data-center IPs.

## Later option
Move the server to a single cloud VM (~$25–60/mo), with clips on R2, SQLite backed up
by Litestream, and Tailscale access. The app only changes its base URL.
See `docs/cloud-architecture-plan.md`.
