# HANDOFF — Fantasy Health Edge

**Read this first when resuming work.** It records the verified facts, the
decisions and their reasoning, the bugs already found, and the ordered backlog —
so none of it has to be re-derived.

- **Last updated:** 2026-08-23
- **Branch:** `master`, 15 commits, working tree clean
- **Governing spec:** [`docs/MASTER_BUILD_DIRECTIVE.md`](docs/MASTER_BUILD_DIRECTIVE.md)

---

## 0. Standing instruction from the user

> **Claude takes no credit for the code.**

No `Co-Authored-By` trailers, no "generated with" footers, no AI attribution in
source or docs. Every commit so far follows this. `CLAUDE.md` and `.claude/` are
functional tooling config, not credit.

---

## 1. Resume in one minute

```bash
make quality        # format, lint, mypy, pytest, then the frontend gates
```

Expected: **459 Python tests**, **33 frontend tests**, ruff clean, `mypy --strict`
clean across 109 files, eslint clean, `tsc` clean.

See it run:

```bash
make dev-api        # terminal one → http://localhost:8000
make dev-web        # terminal two → http://localhost:3000
```

Or headless: `./.venv/bin/python -m fhe.cli simulate --seed 42`

---

## 2. Environment (verified, do not re-derive)

| Thing | Value |
| --- | --- |
| Python | **3.14.3** at `/opt/homebrew/bin/python3.14`. Bare `python3` is Anaconda **3.9** — never use it. |
| venv | `./.venv`. **Always invoke `./.venv/bin/python`.** |
| Node | v25.6.0, npm 11.12.0. No pnpm. npm workspaces from the repo root. |
| Docker | Installed; **daemon was not running**. Compose is untested here as a result. |
| Postgres / Redis | Not installed natively — hence the fallbacks. |

---

## 3. Verified external facts — do not re-research

Full detail with measurements in [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).
The headlines that shaped the design:

- **Sleeper** is public and unauthenticated. Documented limit 1000 req/min; we
  self-limit to 600. `/players/nfl` is 14.6 MB (docs say ~5 MB) with 12,221
  players, cached 20 hours.
- **Sleeper's not-found behaviour is inconsistent**: unknown user → HTTP 200
  with `null`; unknown league or draft → 404.
- **`roster_id` on a pick is an integer**, though documented as a string.
- **Sleeper practice fields are populated for 1 player in 12,221.** Unusable.
  Practice data comes from nflverse.
- **Sleeper `gsis_id` covers only 21% of top-200 players**, and nflverse has no
  `sleeper_id`. This measurement is why the crosswalk exists.
- **The directive's claim that nflverse injuries end after 2024 is wrong.**
  Coverage is 2009–**2025**; the 2025 file is complete (6,068 rows, weeks 1–22).
- **The DynastyProcess crosswalk is GPL-3.0** — fetched at runtime into a
  git-ignored cache, never committed. Missing values are the literal string
  `"NA"`.

---

## 4. What is built

```
src/fhe/
├── config.py, observability.py, cli.py
├── core/            ★ PURE — enforced by an AST test
│   ├── types, league, errors
│   ├── injury/      taxonomy (99.97% coverage), practice trajectory
│   ├── health/      heuristic-v1 scorer
│   ├── draft/       state, vorp, scarcity, survival, roster, engine, board, service
│   └── simulation/  synthetic pool, seeded simulator
├── data/
│   ├── identity.py  UUIDv5 resolution, crosswalk, conflicts
│   ├── providers/   base (retry/jitter/limits), sleeper, nflverse
│   └── ingest/      run lineage, sleeper_players, nflverse_injuries, csv_import
├── db/              base, session, upsert, models/ (26 tables)
├── api/             app, deps, errors, middleware, events (SSE), mappers,
│                    services/, routers/ (health, simulations, imports,
│                    diagnostics, sleeper)
└── worker/          draft_poller.py

apps/web/            Next.js 16 war room — see §7
alembic/             initial revision, drift test
.claude/             4 agents, 4 skills
.github/workflows/   4-job CI
docs/                9 documents + 6 ADRs
```

### Bugs already found and fixed — do not reintroduce

Each has a regression test named after the symptom.

1. **Kicker ranked #3 overall** — K/DEF had no replacement baseline, so VORP
   equalled their whole projection.
2. **Round-14 defense scored like the 1.01** — VORP normalised against the
   *available* pool instead of a fixed pre-draft scale.
3. **A 25-pick faller reported 0.01% survival** — fixed by re-anchoring the
   distribution on the current pick.
4. **Superflex QB replacement was QB14**, should be ~QB22.
5. **`"chest"` normalised to REST** — substring matching; now word-boundary regex.
6. **Re-importing projections duplicated every row** — a nullable `week` in a
   unique key cannot deduplicate, because `NULL != NULL`. Now `SEASON_LONG_WEEK`.
7. **External id collisions** — the crosswalk claims one id for two players in 3
   cases out of 24,441. Resolved by confidence, loser reported.
8. **The unresolved-identity metric counted out-of-scope linemen** — reported
   4,147 failures where the real number was 3.
9. **SSE subscriber registered lazily** — an async generator's body runs on
   first `__anext__`, so events published in that window were lost.
10. **`EventSource.onmessage` never fired** — the server emits *named* events.
    The stream connected, reported LIVE, and delivered nothing.
11. **Weekly stats read from a dead release path** — the legacy `player_stats`
    tag 404s for recent seasons; the maintained one is `stats_player`.
12. **Identity resolution discarded identifiers** when a gsis_id came directly
    from Sleeper, halving `pfr_id` coverage and leaving snap counts unjoinable
    for 2,336 players a season.
13. **A live draft reported "drafting" forever** — pick arithmetic cannot know a
    draft ended early, so the provider's own status now wins.
14. `@unique` enum crash from a duplicate alias; `cached_property` incompatible
    with `slots=True`; `user_draft_slot=None` could not express "no human seat".

---

## 5. What is NOT built — resume here

### Next
- **Serving path for the learned model**, if it is ever promoted: a model
  registry, versioned artefacts, and drift monitoring. `docs/MODEL_CARD.md`
  records why the model passes its bar and is still not in production.
- **Docker Compose verification** — never run, because the daemon was down

### Done since the last handoff
- Command palette (Cmd/Ctrl+K), favourites, and a light/system theme toggle,
  closing directive §22 and §10. Selecting a player now reveals its row.
- **Engine fix — sub-replacement players are scored on a signed scale.** The
  value component clamped at zero, so everyone below replacement tied on the
  heaviest term and the ADP term ordered them worst-first: a quarterback 124
  points below the QB12 baseline outranked QB1. Found by end-to-end testing.
- Live Sleeper draft wired end to end: connect, DB-backed player pool, poller
  supervision, and a unified `/drafts/{id}` API serving live and simulated
  drafts identically.
- Weekly stats and snap counts ingested, so the health model's workload terms
  are measured rather than absent.
- Ten automated data-quality checks writing to `data_quality_results`.
- Playwright end-to-end tests (now twelve), starting both servers themselves.
- The ML phase: point-in-time dataset, seven-check leakage audit, baselines,
  temporal evaluation, and calibration — with an honest not-promoted verdict.

---

## 6. Decisions and their reasoning

Recorded as ADRs in [`docs/adr/`](docs/adr/): single Python distribution,
additive scoring, zero-infrastructure fallbacks, the runtime crosswalk, SSE over
WebSockets, and the static VORP baseline. Read those before changing any of them.

---

## 7. Frontend notes

- Design is a "broadcast control room": warm near-black, sodium-vapor amber,
  Saira Condensed for labels, IBM Plex Mono for every number.
- **Risk is encoded three ways** — glyph, word, colour. Keep it that way.
- **The board is always read from the server.** Events trigger a refetch; they
  never patch local state.
- SSE is tested against a real uvicorn server. httpx's `ASGITransport` never
  sends `http.disconnect`, so a streaming response never completes under it.

---

## 8. Conventions

See [`CLAUDE.md`](CLAUDE.md). The load-bearing ones: core stays pure, every
score decomposes, missing data lowers confidence rather than inventing risk, raw
provider text is never discarded, and no test is ever weakened to get green.
