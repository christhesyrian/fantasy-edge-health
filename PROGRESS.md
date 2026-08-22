# Build progress log

Tracks phases from [`docs/MASTER_BUILD_DIRECTIVE.md`](docs/MASTER_BUILD_DIRECTIVE.md) §39.
A phase is only marked complete when it meets the directive's §38 definition of
done: implemented, formatted, linted, type-checked, tested, documented.

## Status

| Phase | State | Notes |
| --- | --- | --- |
| 0 — Research & architecture | **Done** | Environment, Sleeper, nflverse, and the ID crosswalk all verified live. Findings in `HANDOFF.md` §3. Claude agent/skill config still outstanding. |
| 1 — Repository foundation | **Partial** | Monorepo layout, config, logging, metrics, DB schema, quality gates. Missing: Alembic, Docker Compose, CI, Makefile. |
| 2 — Player data | **Partial** | Identity resolution built and measured. Missing: the ingestion jobs that write to the database. |
| 3 — Historical data | Not started | Providers can read nflverse; nothing persists it yet. |
| 4 — Health intelligence | **Partial** | Taxonomy + heuristic scorer done and tested. Missing: API and UI. |
| 5 — Rankings | **Partial** | VORP, scarcity, tiers, risk adjustment done. Missing: ADP/projection import, UI. |
| 6 — Draft engine | **Done (domain)** | Engine, survival model, explanations, board assembly. Needs API exposure. |
| 7 — Mock draft | **Partial** | Deterministic simulator drives the real engine. Missing: web UI. |
| 8 — Sleeper live draft | Not started | Provider ready; poller, event bus, and SSE outstanding. |
| 9 — ML | Not started | Blocked on Phase 3 features, as the directive requires. |
| 10 — Product polish | Not started | |
| 11 — Production readiness | Not started | |

## Quality gates

Current on `master`: **295 tests pass**, `ruff check` clean, `ruff format --check`
clean, `mypy --strict` clean across 42 source files.

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
