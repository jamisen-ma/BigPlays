# BigPlays mobile (Expo Go)

A personal iPhone app for BigPlays: an ESPN-style scoreboard and play-by-play for NFL and MLB, with video clips attached to viral plays, plus the clip library. It runs in **Expo Go only**. You don't need a paid Apple account, EAS builds, the App Store, or push notifications.

- **Expo SDK 57** (React Native 0.86, React 19.2). The App Store build of Expo Go only runs the current SDK.
- expo-router (tabs + stack), expo-video for playback, expo-image for logos and posters, AsyncStorage for settings.
- The pure logic lives in `src/shared/`. It is ported from `frontend/src/games/*` and `frontend/src/components/util.ts` and unit-tested with jest-expo.

| Screen | What it does |
| --- | --- |
| **Scores** | NFL/MLB toggle and prev/next day (tap the date to jump to today). Game cards show logos, score, live pill, MLB diamond/count/outs, NFL down & distance with possession, and a 🎬 clip badge. Live games sort first. Pull to refresh. Polls every 15 s while any game is live. |
| **Game** | Header score and situation, a horizontally scrolling linescore, and play-by-play grouped by period (latest first, toggleable). Viral plays show a poster that expands into an inline player, with alternate clips and a source badge (LIVE CAPTURE / OFFICIAL UPLOAD / REPLAY). Has a "Viral only" toggle and a "More clips from this game" section. Polls every 8 s while live and flashes "NEW CLIP" when a clip attaches. |
| **Clips** | The `/api/highlights` library, newest first, filterable by league. Tap a clip to play it full screen. YouTube-only items open in the browser. |
| **Settings** | Shows the API URL in use, where it came from, and live connection status (`/api/status`). You can override the URL; the override is saved on the phone. |

## How the app finds the backend

The API base is resolved in this order (`src/shared/apiBase.ts`):

1. **Settings override**, saved in AsyncStorage.
2. **`EXPO_PUBLIC_API_URL`**, read when Metro starts, e.g. `EXPO_PUBLIC_API_URL=http://100.85.158.89:8765`.
3. **The Metro host.** The app takes the host the phone loaded the JS bundle from (`expoConfig.hostUri`). The port is `EXPO_PUBLIC_API_PORT` if set; otherwise **8765** for a Tailscale host (100.64.0.0/10 or `*.ts.net`, which goes through `scripts/tailnet_proxy.py`) and **8000** for anything else (LAN or localhost web).
4. `http://127.0.0.1:8000`. This only works in a simulator or web on the Mac.

Clip and poster URLs come back relative (`/clips/<file>`) and are prefixed with the same base.

## Backend reachability (Tailscale)

The backend stays on **127.0.0.1:8000**. `scripts/tailnet_proxy.py` (run by the lead) forwards **100.85.158.89:8765 -> 127.0.0.1:8000** on the Mac's Tailscale IP, so the phone's API URL is **`http://100.85.158.89:8765`**. Don't use port 8000 from the phone; `*:8000` is held by an unrelated process. `tailscale serve` doesn't work with the Mac App Store build of Tailscale, which is why the proxy exists. CORS is `*`, so the web preview works too.

## Run it on your iPhone (Expo Go, SDK 57)

One-time setup:

1. Install **Expo Go** from the App Store. It must support SDK 57, which is the current `expo@latest`.
2. Install **Tailscale** on the iPhone and sign in to the same account as the Mac. The Mac is `100.85.158.89`.
3. `cd mobile && npm install`

Each session:

```bash
# on the Mac: backend on 127.0.0.1:8000 and scripts/tailnet_proxy.py (:8765) must be running
cd mobile
npm run start:tailscale
#  = REACT_NATIVE_PACKAGER_HOSTNAME=100.85.158.89 EXPO_PUBLIC_API_URL=http://100.85.158.89:8765 expo start
#  Metro prints: exp://100.85.158.89:8081
```

On the iPhone:

1. Turn **Tailscale** on.
2. Open **Expo Go**.
3. Scan the terminal QR code with the Camera app (or use `mobile/expo-go-qr.png` if it exists), or in Expo Go tap "Enter URL manually" and type `exp://100.85.158.89:8081`.
4. If Scores says "Can't reach BigPlays at ...", go to **Settings**, set the API URL to `http://100.85.158.89:8765`, tap **Test**, then **Save**.

If the Mac's Tailscale IP ever changes, get it with `/Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4` and update the `start:tailscale` script.

**Same Wi-Fi instead:** `npx expo start`. The API then defaults to `http://<mac-lan-ip>:8000`, which only works if the backend listens on that interface.

**Metro fallback:** `npm run start:tunnel` serves only the JS bundle through an Expo tunnel. The API must still be reachable, so set `EXPO_PUBLIC_API_URL` or use the Settings override.

## Development checks

```bash
cd mobile
npx tsc --noEmit                 # typecheck
npm test                         # jest-expo unit tests (src/shared/__tests__)
npx expo export --platform ios   # production bundle sanity check (npm run export:ios)
npx expo-doctor                  # dependency / config sanity
npm run web:local               # = EXPO_PUBLIC_API_URL=http://127.0.0.1:8000 expo start --web
```

## Layout

```
src/app/                  expo-router routes
  _layout.tsx             stack + dark theme + ApiProvider
  (tabs)/index.tsx        Scores
  (tabs)/clips.tsx        Clip library
  (tabs)/settings.tsx     API URL override + status
  game/[league]/[id].tsx  Game detail / play-by-play
  player.tsx              Full-screen player (modal)
src/components/           RN UI (GameCard, Situation, Linescore, PlayRow, ClipCard, VideoPlayer, ui)
src/lib/                  ApiContext (base URL), hooks (polling), theme tokens
src/shared/               pure TS ported from the web app: types, format, linescore, source, api, apiBase
```
