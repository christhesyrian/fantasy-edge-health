# Handoff to v0 — frontend UI/UX

You are taking over the **look and feel** of Fantasy Health Edge. The backend,
the recommendation engine, the data pipeline, and the API contract are finished
and working. This document tells you exactly what you may change, what you must
not, and how to run and verify your work.

**Read §1 and §9 before you touch anything.**

---

## 1. The one rule

> **The frontend renders. It never computes.**

Every score, rank, probability, risk figure, tier, alert, and explanation is
produced by the Python engine and arrives as JSON. There is no fallback path in
which TypeScript decides what to recommend.

Concretely, do not write client-side code that:

- ranks, sorts by, or re-weights players by anything other than a field the API
  already returned;
- computes value over replacement, scarcity, survival probability, roster need,
  ADP value, or availability risk;
- derives a recommendation label (`TAKE`, `VALUE`, `WAIT`, `AVOID`, …);
- composes the wording of a reason or a score explanation;
- decides whether a player is available, drafted, or on your roster.

Sorting the table by a column the API returned is fine. Filtering a list you
were given is fine. Formatting `0.42` as `42%` is fine. Deciding *that* 0.42 is
the right number is not.

Why this is absolute: the same engine drives the live draft, the simulator, and
the test suite. If the browser computed anything, a rehearsal would stop being
a rehearsal, and two implementations would drift on draft night.

## 2. What you may freely change

**Everything inside `apps/web/src/` that is presentation.** Specifically:

| Path | Freedom |
| --- | --- |
| `src/app/globals.css` | Full. Design tokens, palette, type scale, spacing. |
| `src/app/layout.tsx`, `src/app/page.tsx` | Full. |
| `src/components/ui/**` | Full. |
| `src/components/war-room/**` | Full for markup, layout, styling, animation. Keep the data flow (§5). |
| `src/components/Onboarding.tsx`, `ConnectSleeper.tsx` | Full for presentation. Keep the calls they make. |
| New routes under `src/app/**` | Encouraged — see §11. |

**Do not casually modify:**

| Path | Why |
| --- | --- |
| `src/lib/types.ts` | The zod schemas *are* the API contract. Changing one to make an error go away hides a real mismatch. |
| `src/lib/api.ts`, `src/lib/apiError.ts` | The client. Add a method when you need a new endpoint; do not change how responses are validated. |
| `src/lib/useDraftStream.ts` | Reconnection, gap detection, and staleness. Subtle and load-bearing (§6). |
| `src/lib/preview/**` | The offline preview backend (§8). `recorded.json` is generated — never hand-edit it. |
| Anything outside `apps/web/` | Backend. Not yours this pass. |

## 3. Architecture

```
src/
├── app/
│   ├── layout.tsx              root layout, fonts, providers
│   ├── page.tsx                "/"  → <Onboarding />
│   ├── globals.css             Tailwind v4 @theme — all design tokens
│   └── war-room/[id]/page.tsx  "/war-room/:id" → <WarRoom simulationId>
├── components/
│   ├── Providers.tsx           TanStack Query client
│   ├── Onboarding.tsx          league setup + demo start
│   ├── ConnectSleeper.tsx      3-step Sleeper connect
│   ├── ui/
│   │   ├── Panel.tsx
│   │   └── RiskBadge.tsx       risk encoded as glyph + number + colour
│   └── war-room/
│       ├── WarRoom.tsx         the only stateful container
│       ├── TopRail.tsx         identity, league, pick, feed status
│       ├── AlertRail.tsx       engine-generated alerts
│       ├── BestAvailable.tsx   the board (dense table)
│       ├── RecommendationPanel.tsx  focused pick + score breakdown
│       ├── HeadlinePicks.tsx   best / safest / highest upside
│       ├── MyRoster.tsx        roster slots
│       ├── ScarcityStrip.tsx   positional scarcity
│       ├── DraftTicker.tsx     recent picks
│       ├── CompareTray.tsx     2–4 player comparison
│       ├── PlayerDrawer.tsx    full detail, timeline, limitations
│       └── CommandPalette.tsx  ⌘K
└── lib/
    ├── api.ts        typed client; picks live or preview backend
    ├── apiError.ts   ApiError
    ├── types.ts      zod schemas + inferred types  ← the contract
    ├── useDraftStream.ts  SSE with gap detection
    ├── useFavourites.ts   localStorage, useSyncExternalStore
    ├── useTheme.ts        dark / light / system
    ├── format.ts, cn.ts
    └── preview/      offline fixture backend
```

Stack: Next.js 16 (App Router, Turbopack), React 19, TypeScript strict (no
`any`), Tailwind v4 (CSS-first `@theme`, no config file), TanStack Query, zod.

## 4. The API contract

Base URL: `NEXT_PUBLIC_API_BASE_URL`, default `http://localhost:8000`.
Interactive reference while the API runs: `http://localhost:8000/docs`.

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/v1/health` | `HealthStatus` — includes `degradations[]` |
| POST | `/api/v1/simulations` | `SimulationState` |
| GET | `/api/v1/simulations/{id}` | `SimulationState` |
| POST | `/api/v1/simulations/{id}/advance` | picks made |
| POST | `/api/v1/simulations/{id}/pick` | the pick |
| POST | `/api/v1/simulations/{id}/reset` | `SimulationState` |
| GET | `/api/v1/drafts/{id}` | `DraftState` (+ live poller health) |
| GET | `/api/v1/drafts/{id}/board?depth=` | **`DraftBoard`** — the whole war room |
| GET | `/api/v1/drafts/{id}/players/{uuid}` | `PlayerDetail` |
| GET | `/api/v1/drafts/{id}/compare?player_uuid=…` | `PlayerDetail[]` |
| GET | `/api/v1/drafts/{id}/events` | SSE stream |
| POST | `/api/v1/drafts/{id}/disconnect` | 204 |
| GET | `/api/v1/players` | `PlayerPage` — browse, filter, page (no draft needed) |
| GET | `/api/v1/players/{uuid}` | `PlayerDetail` (no draft needed) |
| GET | `/api/v1/sleeper/state`, `/users/{u}`, `/users/{id}/leagues`, `/leagues/{id}/drafts` | onboarding lookups |
| POST | `/api/v1/leagues/connect` | `ConnectedDraft` |

`/drafts/{id}` serves **both** simulations and live Sleeper drafts identically —
that is why the board component does not branch on which it is showing.

**Types and schemas live in `src/lib/types.ts`.** Every response is parsed
through its zod schema in `api.ts`, so a contract change surfaces as a
validation error naming the field rather than a runtime surprise. If you see one
of those, the fix is almost never to loosen the schema.

## 5. Authoritative state

**The server is authoritative. The board is always read, never patched.**

```
event arrives ─► refetchBoard() ─► GET /board ─► render
```

`WarRoom.tsx` never mutates a local copy of the board. An optimistically
patched board that drifts from the engine's answer is worse than one that lags
by 200 ms, because the user cannot tell which they are looking at.

Client-side state is deliberately limited to things the server has no opinion
about:

| State | Where | Persisted |
| --- | --- | --- |
| Board, roster, alerts, scarcity, picks | TanStack Query `["board", id]` | server |
| Selected / inspected player | `WarRoom` `useState` | no |
| Comparison list (max 4) | `WarRoom` `useState` | no |
| Filter, palette open | `WarRoom` `useState` | no |
| Favourites | `useFavourites` | `localStorage` |
| Theme | `useTheme` | `localStorage` |

Favourites and theme use `useSyncExternalStore` with an empty server snapshot,
so SSR markup matches the client's first paint. If you refactor them, keep that
property or you will introduce a hydration mismatch.

## 6. Server-sent events

`useDraftStream(url, { onEvent, onResyncRequired })` returns
`{ connection, lastEventAt, lastSequence, eventCount, attempts }`.

`connection` is `LIVE | RECONNECTING | STALE | DISCONNECTED | PREVIEW` and is
rendered in `TopRail`. Three behaviours you must not regress:

1. **Events are *named*** (`event: pick_made`). `EventSource.onmessage` fires
   only for unnamed events, so each type has an explicit listener. Getting this
   wrong is silent — the stream connects, reports healthy, and delivers nothing.
2. **Gaps are detected, not papered over.** Every event carries a monotonic
   `sequence`; a gap triggers a canonical re-read rather than a guess.
3. **Silence is not health.** No traffic for 40 s (heartbeat is 15 s) reports
   `STALE`, because an open-but-dead socket looks exactly like a quiet one.

**Never show `LIVE` when nothing is connected.** This is a product rule, not a
style choice: a manager trusting a stale board loses picks.

## 7. Live mode vs demo mode

Both use the same components and the same endpoints.

- **Demo** — `POST /simulations` creates a seeded synthetic draft. `board.is_demo`
  is `true` and the UI shows a `Demo · synthetic data` badge. Advance, reset,
  and pick all work. No credentials, no database, no ingestion.
- **Live** — `POST /leagues/connect` with a Sleeper league and draft. A poller
  follows the provider and pushes picks over SSE. `is_demo` is `false`, no
  simulator controls, and `DraftState.poller` carries real connection health.

Demo data must be labelled wherever it appears. Do not remove those badges.

## 8. Preview mode — how you get a working UI with no backend

You will not be able to run Python, PostgreSQL, and a provider poller in a v0
preview. So the frontend ships an explicit offline mode.

```bash
NEXT_PUBLIC_PREVIEW_MODE=fixtures
```

What it does: `src/lib/api.ts` resolves `api` to `previewApi` instead of the
HTTP client. `previewApi` replays `src/lib/preview/recorded.json` — **real
responses captured from the real FastAPI app and the real engine**, recorded by
`fhe preview capture`. It is 12 board snapshots one pick apart, plus 16 player
details, validated through the same zod schemas as a live response.

Nothing in the preview computes a score. It selects an already-computed
snapshot. That is what keeps §1 true even offline.

**What works:** the whole board, score breakdowns, alerts, scarcity, roster,
ticker, advancing pick by pick (`n`) or to your turn (`a`), reset, the player
drawer, comparison, favourites, themes, the command palette, filtering, search.

**What is refused, loudly:** drafting a player of your choosing (the recording
holds the picks the simulator actually made, so honouring an arbitrary click
would show the wrong player as drafted), and connecting a Sleeper league.

**How it is prevented from lying:**

- a `PREVIEW · SYNTHETIC FIXTURES` badge in the top rail;
- the feed indicator reads `PREVIEW`, never `LIVE`;
- `/health` reports preview as a named degradation, so the landing page shows
  "running in degraded configuration";
- the Sleeper card is replaced by an explanation instead of a form that fails;
- the flag is compile-time, so a build without it cannot reach preview code.

If you change a zod schema, **re-record the fixtures** (`fhe preview capture`)
or preview will fail validation — which is the point.

## 9. Testing

Run from `apps/web/`:

```bash
npm run typecheck     # tsc --noEmit, strict
npm run lint          # eslint
npm run format        # prettier --write
npm run test          # vitest — component + hook tests
npm run build         # production build
npm run e2e           # Playwright; starts API + web itself
```

Everything must pass before a change is done. Do not delete or weaken a test to
get green — if a test fails, either the change is wrong or the test encodes an
outdated rule, and the second needs saying out loud.

**Guaranteed coverage you must keep passing** (`e2e/demo-draft.spec.ts`,
`e2e/preview.spec.ts`, and the component suites):

1. demo onboarding with no credentials
2. mock draft starts
3. picks advance and the board reacts
4. a drafted player leaves the board
5. recommendations change as the draft moves
6. the roster fills
7. the player drawer opens with health, timeline, and limitations
8. two players compare side by side
9. favourites persist across a reload
10. the command palette opens and finds players and commands
11. reconnect / resync state is reported
12. the UI never reports a live connection when there is none

If your redesign renames a `data-testid`, update the test in the same change.

## 10. Keyboard shortcuts

Preserve these — the war room is used under a clock.

| Key | Action |
| --- | --- |
| `⌘K` / `Ctrl+K` | Command palette (works even while typing — that is how you get *out* of a field) |
| `n` | Advance one pick (demo) |
| `a` | Advance to my pick (demo) |
| `Enter` | Draft the selected player (demo) |
| `f` | Toggle favourite on the selected player |
| `i` | Open the selected player |
| `Esc` | Close drawer / palette |

## 11. Pages you may want to add

The API already exposes what these need, so they are UI work only:

- **`/rankings`** — `GET /api/v1/players` (browse, filter by position, search,
  page), or a draft board via `GET /drafts/{id}/board` when league-specific
  ranking is wanted. Remember: ranking *is* league-specific, so a global
  rankings page shows projections and health, not draft recommendations.
- **`/health`** (health centre) — `GET /api/v1/players` returns each player's
  full `health` object: score, band, itemised contributions, and limitations.
  `GET /api/v1/players/{uuid}` gives the injury timeline and workload.
- **Richer comparison** — `GET /drafts/{id}/compare?player_uuid=…` already
  returns full `PlayerDetail` for 2–4 players.
- **Deeper player detail** — the same `PlayerDetail` the drawer renders.

Both `/players` endpoints work without a draft, which is precisely why they
exist. They are **not** available in preview mode — build those screens against
a local API.

## 12. Product language rules

These are non-negotiable and appear in the acceptance criteria:

- "Elevated availability risk", never "will get injured". It is not a medical
  model and must never read as one.
- **Every health figure carries its limitations.** The API returns them; render
  them, do not truncate them away.
- **Every score decomposes.** `components` sums to the headline and a test
  asserts it. Never show the headline without a route to the breakdown.
- **Missing data lowers confidence; it never invents risk.** An unmeasured
  player is *unknown*, not *safe*.
- **Risk is encoded three ways** — glyph, number, colour — so it survives
  colour-blindness and a bad monitor. Keep all three.
- **Degradations are visible.** If the API says it is degraded, say so.

## 13. Accessibility

Currently in place and worth keeping: semantic landmarks, `aria-label` on icon
buttons, `aria-pressed` on toggles, `role="status"` for notices, `role="dialog"`
with `aria-modal` for the drawer and palette, visible focus, screen-reader text
for risk values, and `prefers-reduced-motion` honoured for the row animations.

Contrast is currently AA in dark mode. If you restyle, re-check it — the amber
accent on near-black is easy to break.

---

## 14. Importing this repository into v0

This is a **monorepo**. The Next.js app is one directory inside it; the rest is
Python.

### Recommended workflow

1. **Import the repository** — v0 → attachment menu → Git Import → paste
   `https://github.com/<your-org>/fantasy-edge-health` (or pick it from the
   account dropdown).
2. **Base branch**: `master`.
3. **Root directory**: **`apps/web`** ← this is the important one.
4. **Environment variables**: see the table below.
5. **Preview**: v0 builds and serves the Next.js app.

### Why root directory `apps/web`, not the repository root

- `apps/web` is self-contained: its own `package.json` **and** its own
  `package-lock.json`, with no workspace-relative dependencies. It installs and
  builds on its own.
- Pointing at the repository root makes the deployment look like a Python
  project with a Node app buried in it, and surfaces backend configuration
  (`FHE_DATABASE_URL`, `FHE_REDIS_URL`, `FHE_ENV`, …) that the frontend never
  reads.
- The two `.env.example` files are split for exactly this reason: the root one
  is backend-only, and `apps/web/.env.example` contains only what the Next.js
  app reads.

### Environment variables to enter in v0

**For a fixtures preview — the recommended starting point:**

| Variable | Value |
| --- | --- |
| `NEXT_PUBLIC_PREVIEW_MODE` | `fixtures` |

**That is the only variable you need.** `NEXT_PUBLIC_API_BASE_URL` is ignored in
preview mode, because no network call is made. You get a fully interactive war
room with no backend.

**For live mode against a deployed API:**

| Variable | Value |
| --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `https://your-api-host.example` — no trailing slash |
| `NEXT_PUBLIC_PREVIEW_MODE` | leave unset |

This requires a deployed FastAPI backend reachable over HTTPS, with the v0
preview origin listed in its `FHE_CORS_ORIGINS`. See
[`DEPLOYMENT.md`](DEPLOYMENT.md). If you do not have one, use fixtures.

**Never set** any `FHE_*` variable in v0. They are backend settings and the
frontend reads none of them.

### Expected preview behaviour

With `NEXT_PUBLIC_PREVIEW_MODE=fixtures`:

- The landing page renders, with "running in degraded configuration · 1"
  naming preview as the reason.
- "Enter the war room" navigates to `/war-room/preview` and shows a full board:
  40 players, scores, risk badges, survival probabilities, alerts, scarcity.
- The top rail shows `PREVIEW · SYNTHETIC FIXTURES` and a feed state of
  `PREVIEW`.
- `n` advances one pick; `a` runs to the user's turn at pick 5. The board,
  ticker, and roster all react.
- The drawer, comparison, favourites, themes, and ⌘K all work.
- "Draft now" explains why it is unavailable instead of failing.
- After 12 picks the recording ends and says so. Reset starts it again.

### Commands to run after making changes

From `apps/web`:

```bash
npm run typecheck && npm run lint && npm run format && npm run test && npm run build
```

And, if you can run the Python API locally, the end-to-end suite:

```bash
npm run e2e
```

---

## 15. Quick reference

| Question | Answer |
| --- | --- |
| v0 import root | `apps/web` |
| Branch | `master` |
| Env vars for preview | `NEXT_PUBLIC_PREVIEW_MODE=fixtures` (only) |
| Env vars for live | `NEXT_PUBLIC_API_BASE_URL=https://…` |
| Does preview use the real API? | **No.** Recorded real-engine output, replayed offline and labelled synthetic. |
| Can I add UI logic? | Presentation yes; recommendation logic never. |
| Where is the contract? | `src/lib/types.ts` |
| Where is state? | Server, read through TanStack Query. |
| Local backend | `make dev-api` → `http://localhost:8000` |
| Local frontend | `make dev-web` → `http://localhost:3000` |
