# Pre-v0 audit

A requirement-by-requirement audit of what is **actually in the repository**,
checked against the code rather than against `PROGRESS.md`. Written before
handing the frontend to v0, so nothing is claimed that a reviewer cannot find.

- **Date:** 2026-08-23
- **Commit:** the tree at the time of the pre-v0 stabilisation pass
- **Governing spec:** [`MASTER_BUILD_DIRECTIVE.md`](MASTER_BUILD_DIRECTIVE.md)

**Method.** Every row below was verified by reading the implementation and, where
behaviour was in question, by running it. Where a document disagreed with the
code, the code won and the document was corrected (§4).

**Legend** — COMPLETE · COMPLETE BUT NEEDS POLISH · PARTIAL · NOT IMPLEMENTED ·
INTENTIONALLY DEFERRED

---

## 1. Quality gate, as measured

Run at the start of this pass, before any change:

| Gate | Result |
| --- | --- |
| `ruff check src tests` | pass |
| `ruff format --check` | 118 files formatted |
| `mypy --strict` | pass, 118 source files |
| `pytest` | **515 passed** |
| prettier `--check` | pass |
| eslint | pass |
| `tsc --noEmit` | pass |
| vitest | **55 passed** |
| `next build` | pass |
| Playwright | **12 passed** |

Nothing was failing beforehand. `HANDOFF.md` claimed 459 Python and 33 frontend
tests; both were stale, and are corrected in §4.

---

## 2. Requirement audit

### Data and ingestion

| Requirement | State | Evidence |
| --- | --- | --- |
| Sleeper integration (players, leagues, drafts, picks) | **COMPLETE** | `data/providers/sleeper.py`, contract tests against saved fixtures. Public, unauthenticated, self-limited to 600 rpm. |
| Historical injuries | **COMPLETE** | `data/ingest/nflverse_injuries.py`, 2009–2025. Taxonomy validated against 62,915 observations at 99.97% coverage. |
| Weekly stats | **COMPLETE** | `data/ingest/nflverse_workload.py`. `PROGRESS.md` said "missing" — that was stale; corrected. |
| Snap counts | **COMPLETE** | Same module. Unresolved snap-count players reduced from 2,336 to 38 by the identity fix. |
| Identity resolution | **COMPLETE** | `data/identity.py`. UUIDv5 on `gsis_id`, runtime GPL crosswalk, conflicts recorded in `player_identity_conflicts` rather than guessed. 100% of top 200. |
| ADP / projections | **COMPLETE** (by CSV import) | `data/ingest/csv_import.py`, `POST /api/v1/imports/{adp,projections}`. No licensed free API was verifiable, so import is the deliberate path. |
| Idempotent ingestion | **COMPLETE** | Dialect-aware upserts; `SEASON_LONG_WEEK` sentinel because a nullable column in a unique key cannot deduplicate. |
| Raw provider text retained | **COMPLETE** | Provenance columns throughout; `docs/DATA_MODEL.md`. |
| Data-quality checks | **COMPLETE** | Ten checks writing to `data_quality_results`; `fhe quality`. |

### Domain and engine

| Requirement | State | Evidence |
| --- | --- | --- |
| Pure domain core | **COMPLETE** | `core/` with an AST purity test walking every module. |
| Recommendation engine | **COMPLETE** | `core/draft/engine.py`. Additive, decomposable, tested to sum to the headline. |
| Next-pick survival probability | **COMPLETE** | `core/draft/survival.py`, with re-anchoring for fallers. |
| Positional scarcity and tiers | **COMPLETE** | `core/draft/scarcity.py`. |
| VORP with a fixed pre-draft baseline | **COMPLETE** | `core/draft/vorp.py`; ADR 0006. |
| Health intelligence | **COMPLETE** | `core/health/heuristic.py`. Itemised contributions, explicit limitations, `unknown` rather than `safe` when unmeasured. |
| Explainability | **COMPLETE** | Components sum to the score; a test asserts it. No LLM writes a recommendation or a reason. |
| Mock draft simulator | **COMPLETE** | `core/simulation/`. Seeded and deterministic, drives the production engine. |

### Live draft

| Requirement | State | Evidence |
| --- | --- | --- |
| Connect a Sleeper draft | **COMPLETE** | `api/services/league_connect.py`. Works even when the league 404s, by reconstructing roster shape from the draft's own `slots_*`. |
| Live poller | **COMPLETE** | `worker/draft_poller.py` + `api/services/poller_manager.py`. Adaptive interval, jittered backoff, tested against outages, duplicates, out-of-order arrival, conflicts. |
| Unified live/simulated API | **COMPLETE** | `/drafts/{id}` serves both; the client does not branch. |
| SSE | **COMPLETE** | `api/events.py`, named events, monotonic sequence, heartbeat, eager subscriber registration. |
| Retries / reconnection | **COMPLETE** | `useDraftStream.ts`: gap detection, exponential backoff with jitter, staleness detection. |
| **Active session recovery** | **COMPLETE** *(new this pass)* | `api/services/session_recovery.py`. A read of a draft whose in-memory session is gone rebuilds it from the persisted league/draft rows plus the provider's current picks, and restarts the poller. 11 regression tests, including no-duplicate-picks, idempotence, roster equality, status reconciliation, and an endpoint-level restart test. |

### Product surface

| Requirement | State | Evidence |
| --- | --- | --- |
| War room | **COMPLETE** | `components/war-room/`, 13 components. |
| Player detail | **COMPLETE** | `PlayerDrawer.tsx` — risk breakdown, injury timeline preserving provider wording, workload, provenance, limitations. |
| Player comparison | **COMPLETE** | `CompareTray.tsx`, 2–4 players, `GET /drafts/{id}/compare`. |
| Rankings | **COMPLETE** (in-board) / **PARTIAL** (dedicated page) | Ranking is surfaced in the board. There is no `/rankings` route. `GET /api/v1/players` was **added this pass** so one can be built without inventing a draft. |
| **Dedicated Health Center** | **NOT IMPLEMENTED** (UI) | No `/health` route exists. The data is fully exposed — `GET /api/v1/players` returns each player's complete health object, `GET /api/v1/players/{uuid}` the timeline and workload — so this is now UI-only work. Left for v0 deliberately. |
| Command palette | **COMPLETE** | `CommandPalette.tsx`, ⌘K, substring matching, fires even while typing. |
| Favourites | **COMPLETE** | `useFavourites.ts` via `useSyncExternalStore`, localStorage, survives reload. |
| Themes | **COMPLETE** | `useTheme.ts` — dark / light / system. |
| Accessibility | **COMPLETE BUT NEEDS POLISH** | Semantic landmarks, 26 aria attributes, `role="dialog"`/`aria-modal`, screen-reader text for risk, `prefers-reduced-motion` honoured. Contrast is AA in dark mode only; light mode has not been audited, and there is no automated a11y check in CI. |
| Safe product language | **COMPLETE** | "Elevated availability risk", never a claim of injury. Limitations render with every health payload. |
| Demo mode with no credentials | **COMPLETE** | Verified end to end by Playwright. |

### Machine learning

| Requirement | State | Evidence |
| --- | --- | --- |
| Point-in-time dataset | **COMPLETE** | `ml/dataset.py`, 58,202 rows over ten seasons. |
| Leakage audit | **COMPLETE** | `ml/leakage.py`, seven checks, including a structural rebuild-with-later-weeks-removed proof. |
| Evaluation and calibration | **COMPLETE** | `ml/train.py`, temporal splits, Brier + reliability. |
| Model promoted to production | **INTENTIONALLY DEFERRED** | `docs/MODEL_CARD.md`. The best-*ranking* model has a Brier score twice as bad as the base rate. The heuristic stays authoritative. This is the correct outcome and was not disturbed. |
| Model serving infrastructure | **INTENTIONALLY DEFERRED** | No promoted model, so a registry, versioned artefacts, and drift monitoring would be scaffolding around nothing. The deferred architecture is described in the model card. |

### Operations

| Requirement | State | Evidence |
| --- | --- | --- |
| Observability | **COMPLETE** | structlog with request ids, Prometheus counters/histograms/gauges, `/api/v1/metrics`, degradations named on `/health`. |
| CI | **COMPLETE** | `.github/workflows/ci.yml` — python, web, integration, security, docker jobs. |
| Container images | **COMPLETE BUT UNVERIFIED** | `services/api/Dockerfile`, `services/worker/Dockerfile`, `apps/web/Dockerfile`, `docker-compose.yml` with health checks and a dedicated migrate service. |
| **Docker Compose verified** | **PARTIAL** | First real attempt made 2026-08-23. It found and fixed a genuine break — `apps/web/package-lock.json` was out of sync, so `npm ci` failed and the web image could not build. Postgres, Redis, and MinIO images pulled; API and worker images were building when the host disk filled (981 MiB free, 100% capacity) and the daemon stopped responding. **The stack has still never come up**: no containerised migration, no health check against PostgreSQL, no SSE exercised. |
| **Load testing** | **COMPLETE** *(new this pass)* | `src/fhe/loadtest/`, `fhe loadtest`. Real measurements in `docs/PERFORMANCE.md`. |
| **Deployment architecture** | **COMPLETE** *(new this pass)* | `docs/DEPLOYMENT.md`. |
| Migrations | **COMPLETE** | Alembic with a drift test. |
| Security | **COMPLETE** | `docs/SECURITY.md`, CORS pinned to exact origins, no secrets committed, dependency and secret scanning in CI. |

### Frontend contract safety

| Requirement | State | Evidence |
| --- | --- | --- |
| Zod response schemas | **COMPLETE** | `lib/types.ts`; every response parsed in `api.ts`. |
| Frontend API types | **COMPLETE** | Inferred from the schemas, so they cannot drift from validation. |
| Contract fixtures | **COMPLETE** *(new this pass)* | `lib/preview/recorded.json`, recorded from the real API and validated by the same schemas. |
| Component tests | **COMPLETE** | 55 vitest tests. |
| SSE tests | **COMPLETE** | `useDraftStream.test.ts`, plus API-side tests against a real uvicorn server. |
| Playwright smoke coverage | **COMPLETE** | 12 live-path + 11 preview-path tests. |

---

## 3. What is genuinely incomplete

Stated plainly, with no euphemism:

1. **The Docker Compose stack has never come up.** Building it was attempted
   for the first time on 2026-08-23; it fixed a real lockfile break and then
   stopped because the host disk was 100% full. Migrations, health checks, and
   SSE have never been exercised in containers.
2. **No dedicated Health Center or Rankings page.** The API supports both; the
   routes do not exist. Deliberately left for v0.
3. **Accessibility is AA in dark mode only.** Light mode contrast is unaudited
   and CI has no automated a11y check.
4. **Multi-worker deployment is unsupported.** Sessions and pollers are
   per-process. One worker is the supported configuration, which
   `docs/PERFORMANCE.md` shows is ample. Documented in `docs/DEPLOYMENT.md` §7.
5. **Board evaluation blocks the event loop.** Measured: `health` latency rises
   from 1.6 ms to 291 ms as concurrency goes 1 → 50. Fine at realistic load, and
   the fix (cache the board per session between picks) is identified but not
   done.
6. **No promoted ML model**, by design.
7. **PostgreSQL is unmeasured.** All performance numbers are against SQLite,
   because of (1).
8. **Preview fixtures ship as a 686 KiB chunk** in every build. It is never
   referenced by the app manifest and never fetched unless preview mode is on,
   but it is present in the output.

---

## 4. Documentation corrections made this pass

Each of these was a case of a document describing a state the code had moved
past.

| Document | Was | Now |
| --- | --- | --- |
| `PROGRESS.md` | Phase 3 "Missing: weekly stats and snap counts" | Both are implemented; phase marked done. |
| `PROGRESS.md` | Phase 8 "not yet wired to a war-room session or the UI" | Wired end to end. |
| `PROGRESS.md` | Phase 9 "Not started" | Complete, with an honest negative verdict. |
| `PROGRESS.md` | 411 Python / 33 frontend tests | Measured counts. |
| `HANDOFF.md` | "459 Python tests, 33 frontend tests", "15 commits" | Measured counts. |
| `USER_ACTION_REQUIRED.md` | "once onboarding ships (Phase C/E)", "once the importer ships (Phase A)", "Once `docker-compose.yml` exists (Phase G)" | All three exist; future-tense removed. |
| `README.md` | Roadmap listing five items, all of which were done | Replaced with what is actually next. |
| `README.md` | "Draft sessions live in memory. They do not survive an API restart." | Live drafts now recover; the limitation is restated accurately. |

---

## 5. Verdict

The repository is in better shape than its own documentation claimed. The gaps
that remain are real but bounded, and none of them blocks a frontend engineer:
the API contract is stable, typed, and validated at both ends, and the offline
preview path means UI work needs no backend at all.
