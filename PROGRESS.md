# Build progress log

Tracks phases from [`docs/MASTER_BUILD_DIRECTIVE.md`](docs/MASTER_BUILD_DIRECTIVE.md) §39.
A phase is only marked complete when it meets the directive's §38 definition of
done: implemented, formatted, linted, type-checked, tested, documented.

## Status

| Phase | State | Notes |
| --- | --- | --- |
| 0 — Research & architecture | **Done** | Providers verified live; ADRs written; Claude agents and skills in place. |
| 1 — Repository foundation | **Done** | Monorepo, config, logging, metrics, 26-table schema, Alembic with a drift test, Docker, CI, Makefile. |
| 2 — Player data | **Done** | Identity resolution measured at 100% of the top 200; Sleeper sync writes players, external ids, health, and conflicts. |
| 3 — Historical data | **Partial** | Injury and practice ingestion done for 2009–2025. Missing: weekly stats and snap counts. |
| 4 — Health intelligence | **Done** | Taxonomy, heuristic scorer, API, and the war-room drawer with timeline and limitations. |
| 5 — Rankings | **Done** | VORP, scarcity, tiers, risk adjustment, CSV import for ADP and projections, rankings surfaced in the board. |
| 6 — Draft engine | **Done** | Engine, survival model, deterministic explanations, board assembly, exposed over HTTP. |
| 7 — Mock draft | **Done** | Seeded simulator drives the production engine; full war room over SSE. |
| 8 — Sleeper live draft | **Partial** | Onboarding endpoints and a tested resilient poller exist; not yet wired to a war-room session or the UI. |
| 9 — ML | Not started | Deliberately blocked on workload features. Promotion bar documented in `docs/MODEL_CARD.md`. |
| 10 — Product polish | **Partial** | Comparison, alerts, keyboard shortcuts, accessibility, reduced motion. Missing: command palette, favourites, light-mode toggle. |
| 11 — Production readiness | **Partial** | Full suite, CI, security scanning, observability, documentation, container images. Missing: load testing, Playwright, a verified Compose run. |

## Quality gates

Current on `master`: **411 Python tests** and **33 frontend tests** pass.
`ruff check` and `ruff format --check` clean, `mypy --strict` clean across 98
files, eslint and `tsc` clean, production build succeeds.

## Log

**2026-08-22**
- Verified the toolchain. Python 3.14.3 is the only 3.12+ interpreter present;
  confirmed the whole dependency stack resolves and installs on it.
- Verified Sleeper endpoints, rate limits, payload sizes, and not-found
  behaviour against live responses rather than trusting the documentation.
  Found three documentation/reality mismatches.
- **Corrected a premise in the directive:** nflverse injury data does *not* end
  after 2024. Coverage is 2009–2025, and the 2025 season is complete.
- Measured player-identity coverage rather than assuming it, which showed a
  direct Sleeper→nflverse join would strand ~79% of the top 200. Added a runtime
  crosswalk, lifting resolution to 100% of the top 200 with zero conflicts.
- Built the pure domain core, the health engine, and the draft engine, and found
  and fixed eight substantive bugs along the way (listed in `HANDOFF.md` §4).
- Built the 26-table schema with provenance columns throughout.

**2026-08-22 (continued)**
- Built the ingestion layer: dialect-aware upserts, run lineage that survives a
  rollback, Sleeper player sync, nflverse injury and practice ingestion, and the
  CSV import path that keeps the product useful with no paid API.
- Found and fixed a NULL-uniqueness bug that silently duplicated every
  re-imported season projection, affecting four tables.
- Added Alembic with a drift test that catches a model changed without a
  revision — the mistake that otherwise only surfaces on a deploy.
- Built the FastAPI layer with SSE, and fixed a race where subscribers
  registered lazily and lost every event published before their first tick.
- Built the war room. Fixed a bug where the stream connected, reported healthy,
  and delivered nothing, because `EventSource.onmessage` ignores named events.
- Verified the whole demo path in a browser against the live API.
- Added the Sleeper onboarding endpoints and a resilient draft poller, with
  tests covering outages, duplicates, out-of-order arrival, and conflicts.
- Wrote the full documentation set and re-verified every quantitative claim in
  it against the running system.
