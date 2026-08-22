# HANDOFF — Fantasy Health Edge

**Read this first when resuming work.** It captures everything needed to
continue without re-deriving decisions or re-researching external providers.

- **Last session ended:** 2026-08-22
- **Branch:** `master` (3 commits, working tree clean)
- **Governing spec:** [`docs/MASTER_BUILD_DIRECTIVE.md`](docs/MASTER_BUILD_DIRECTIVE.md)
  — the original build prompt, copied into the repo verbatim so it is never lost.
  **Re-read it before continuing**; it defines the acceptance criteria.

---

## 0. Standing instruction from the user

> **Claude takes no credit for the code.**

No `Co-Authored-By: Claude` commit trailers, no "Generated with Claude Code" in
PR bodies, no AI attribution in source or docs. Existing commits follow this.
`CLAUDE.md` / `.claude/` are functional tooling config, not credit, and are fine.

---

## 1. How to resume in one minute

```bash
./.venv/bin/python -m pytest -q
```

Expected: **295 passed**. Then:

```bash
./.venv/bin/ruff check src tests && ./.venv/bin/ruff format --check src tests && ./.venv/bin/mypy
```

Expected: all clean, `Success: no issues found in 60 source files`.

See the live board render end to end:

```bash
./.venv/bin/python -c "
from fhe.core.simulation import generate_player_pool, MockDraftSimulator, SimulationConfig
from fhe.core.draft import evaluate_draft, compute_replacement_baseline
from fhe.core.league import LeagueSettings
ls = LeagueSettings.from_tokens(team_count=12, user_draft_slot=5,
    roster_position_tokens=['QB','RB','RB','WR','WR','TE','FLEX','K','DEF']+['BN']*6)
pool = generate_player_pool(); base = compute_replacement_baseline(pool, ls)
sim = MockDraftSimulator(ls, pool, config=SimulationConfig(seed=42)); sim.advance_to_user_turn()
b = evaluate_draft(sim.state, pool, user_draft_slot=5, baseline=base)
print(b.best_pick.name, b.best_pick.overall_score, b.best_pick.recommendation.value)
for c in b.best_pick.components: print(f'  {c.points:+7.2f}  {c.label}: {c.detail}')
"
```

---

## 2. Environment (verified, do not re-derive)

| Thing | Value | Notes |
| --- | --- | --- |
| Python | **3.14.3** at `/opt/homebrew/bin/python3.14` | Only 3.12+ available. `python3` on PATH is Anaconda **3.9** — do not use it. |
| venv | `./.venv` | Created from 3.14.3. **Always invoke `./.venv/bin/python`**, never bare `python3`. |
| Node | v25.6.0, npm 11.12.0 | `pnpm` NOT installed. Use npm, or install pnpm. |
| Docker | installed, **daemon NOT running** | `docker compose` unusable until the user starts Docker Desktop. |
| Postgres / Redis | **not installed natively** | Hence the SQLite + in-process-bus fallbacks. |
| git identity | christhesyrian / cbeshara17@gmail.com | |

Installed and confirmed working on 3.14: fastapi 0.141.1, pydantic 2.13.4,
sqlalchemy 2.0.52, httpx 0.28.1, polars 1.43.2, pyarrow 25.0.1, scikit-learn 1.9.0,
numpy 2.5.2, alembic 1.19.1, structlog, redis, respx, ruff, mypy, pytest.

---

## 3. Verified external facts — DO NOT re-research, DO NOT guess

All verified live on **2026-08-22**. The directive's §47 anti-hallucination rule
applies: anything not listed here must be verified before use.

### Sleeper API (`https://api.sleeper.app/v1`)
- Read-only, **no authentication**.
- Documented limit: *"stay under 1000 API calls per minute, otherwise you risk
  being IP-blocked."* Our self-imposed ceiling is 600 rpm.
- `/players/nfl` is **14.6 MB** (docs claim ~5 MB) and returns **12,221** players.
  Docs say call it *"only once per day at most"*. We cache it on disk.
- **Not-found behaviour is inconsistent** (verified by curl):
  - unknown **user** → HTTP **200**, body `null`
  - unknown **league** → HTTP **404**, body `null`
  - unknown **draft**/picks → HTTP **404**, body `null`
- **`roster_id` on a draft pick is an integer in live responses**, though the docs
  show a string. Both are parsed.
- Live picks carry an **undocumented `reactions` field**.
- `is_keeper` is `null`, not `false`, when unset.
- Current NFL state: season **2026**, `season_type` **"pre"**, week 2.
- Player-payload injury fields and their real coverage (of 12,221):
  `injury_status` 653, `injury_body_part` 575, `injury_notes` 84,
  **`practice_participation` only 1**, `injury_start_date` present but all-null.
  → **Sleeper is not a usable source of practice data.** Use nflverse.

### nflverse (GitHub Releases)
- Stable URL pattern:
  `https://github.com/nflverse/nflverse-data/releases/download/<tag>/<asset>`
- **The directive's claim that injury data ends after 2024 is OUT OF DATE.**
  Verified: `injuries_YYYY.parquet` exists for **2009–2025**. The 2025 file has
  **6,068 rows across weeks 1–22** and the release was rebuilt **2026-03-18**.
  There is no 2026 file — expected, the season hasn't started.
- `snap_counts` 2012–2025; `depth_charts` 2001–2026; `rosters` through 2026.
- `players.parquet` (rebuilt daily; 25,050 rows) has `gsis_id`, `esb_id`,
  `nfl_id`, `pfr_id`, `pff_id`, `otc_id`, `espn_id`, `smart_id` —
  **but NO `sleeper_id`**.
- Injuries schema: `season, game_type, team, week, gsis_id, position, full_name,
  first_name, last_name, report_primary_injury, report_secondary_injury,
  report_status, practice_primary_injury, practice_secondary_injury,
  practice_status, date_modified`.
- **Dirty data to keep handling:** `practice_status` contains literal `"\n    "`
  padding rows and a `"Note"` value. `report_status` includes `"Note"`.

### Identity crosswalk (DynastyProcess)
- `https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv`
- **License: GPL-3.0.** This repo is MIT. → **Never commit this file.** It is
  fetched at runtime into the gitignored `data/cache/`. Documented in the code.
- **Missing values are the literal string `"NA"`** — the single nastiest trap in
  this dataset. `clean_token()` handles it; tests lock it down.
- Coverage measured live: Sleeper carries `gsis_id` for only **~21%** of top-200
  fantasy players. With the crosswalk, resolution reaches **100% of top 200
  (0 conflicts)**, 99.2% of top 400, **97.1% of all 1,038 rostered players**.

---

## 4. What is built (3 commits, all gates green)

```
src/fhe/
├── config.py              Settings; SQLite + in-process-bus fallbacks
├── observability.py       structlog (with secret redaction) + prometheus metrics
├── core/                  ★ PURE DOMAIN — zero I/O, enforced by a test
│   ├── types.py           Position, RosterSlot, ScoringFormat, InjuryDesignation,
│   │                      PracticeStatus, BodyRegion, Recommendation, ...
│   ├── league.py          LeagueSettings, replacement level, snake pick maths
│   ├── errors.py
│   ├── injury/            taxonomy.py (99.97% coverage), practice.py
│   ├── health/            models.py, heuristic.py  (heuristic-v1)
│   ├── draft/             models, state, vorp, scarcity, survival, roster,
│   │                      engine, board, service (evaluate_draft = entry point)
│   └── simulation/        pool.py (synthetic, seeded), simulator.py
├── data/
│   ├── identity.py        IdentityResolver, PlayerCrosswalk, UUIDv5 minting
│   └── providers/         base.py (retry/jitter/rate-limit), sleeper.py, nflverse.py
└── db/                    base.py, session.py, models/ (26 tables)

tests/  unit (8 files) · contract (sleeper) · architecture (purity) · integration (schema)
data/fixtures/  nflverse_injury_descriptors.json · sleeper/*.json (sanitized)
```

### Bugs found and fixed while building — do not reintroduce
1. **Kickers ranked #3 overall.** `VALUED_POSITIONS` excluded K/DEF, so they got
   no replacement baseline and their VORP equalled their entire projection.
   Fixed via `ROSTERABLE_POSITIONS`. Locked by
   `test_kickers_and_defenses_are_not_drafted_early`.
2. **Round-14 defense scored like the 1.01.** `max_vorp` was computed over
   *available* players, so the best remaining player always normalised to 1.0.
   Fixed with `ReplacementBaseline.max_vorp` — a fixed pre-draft scale.
3. **Survival probability said a 25-pick faller was 0.01% likely to last.**
   Fixed by re-anchoring the distribution on the current pick when a player
   falls past their ADP.
4. **Superflex QB replacement was QB14** (should be ~QB22). Weighting flex by
   dedicated slots is wrong for SUPER_FLEX; added `_SUPERFLEX_QB_SHARE = 0.85`.
5. **`"chest"` normalised to REST** via substring matching. All taxonomy
   matching is now word-boundary regex.
6. **`@unique` enum crash** from a duplicate `BodyRegion` alias value.
7. **`cached_property` + `slots=True`** is incompatible — `LeagueSettings` has no
   slots, deliberately, with a comment saying why.
8. **`user_draft_slot=None` couldn't mean "no human seat"** because `or` fell
   back to the league default. Fixed with an `_UNSET` sentinel.

---

## 5. What is NOT built yet — resume here, in this order

### Phase A — Ingestion (next task)
`src/fhe/data/ingest/` — nothing exists yet.
- `sleeper_players.py`: sync Sleeper payload → `players` + `player_external_ids`
  + `current_player_health`, via `IdentityResolver`, writing
  `player_identity_conflicts` and a `data_ingestion_runs` row.
- `nflverse_injuries.py`: seasons 2009–2025 → `injury_events` + `practice_reports`
  (normalise with `fhe.core.injury`; **keep the raw text**).
- `nflverse_stats.py`: weekly stats + snap counts → workload features.
- `crosswalk.py`: download GPL crosswalk into `data/cache/` (never commit).
- `csv_import.py`: **manual ADP/projection CSV import** — directive §8 requires
  this from day one so the product works with no paid API. Validated schemas in
  `data/schemas/`.
- `quality.py`: the checks listed in directive §13, writing `data_quality_results`.

### Phase B — Alembic
`alembic/` does not exist. `alembic init`, point `target_metadata` at
`fhe.db.Base.metadata`, import `fhe.db.models`, generate the initial revision.
Note: schema currently created via `create_all` in tests only.

### Phase C — FastAPI (`src/fhe/api/` — empty)
Endpoints per directive §26. Must include SSE (`/drafts/{id}/events`),
liveness vs readiness split, `Settings.storage_warnings()` surfaced in `/health`,
CORS from `cors_origin_list`, and OpenAPI published for frontend type generation.

### Phase D — Live draft worker (`src/fhe/worker/` — empty)
Poll loop at `draft_poll_interval_seconds` (3s default) → `DraftState.apply_picks`
(already idempotent) → persist → publish event → recompute board → SSE.
Event bus needs a Redis impl + the in-process fallback.

### Phase E — Frontend (`apps/web/` — empty)
Next.js War Room per directive §19–22. **Design the API contract first**, then
build against it. Directive §21 forbids a generic shadcn dashboard and AI-purple
gradients; wants an original dark sports-operations aesthetic.

### Phase F — ML (`src/fhe/ml/` — empty)
Directive §14B. Only after historical features exist. Time-based splits,
leakage audit, calibration, `docs/MODEL_CARD.md`, guarded promotion.

### Phase G — Ops & docs
`.claude/agents` + `.claude/skills` (dirs exist, empty), `.github/workflows/`,
`docker-compose.yml`, `Makefile`, and the docs set in directive §36
(`ARCHITECTURE`, `DATA_SOURCES`, `DATA_MODEL`, `DRAFT_ENGINE`, `INJURY_MODEL`,
`MODEL_CARD`, `RUNBOOK`, `SECURITY`, `INTERVIEW_GUIDE`, ADRs).

**`docs/DATA_SOURCES.md` is overdue** — §4 of the directive requires it, and all
the facts for it are in section 3 above.

---

## 6. Architectural decisions made (record as ADRs when writing docs)

1. **Single Python distribution, `src/fhe/`**, rather than separate packages per
   service. The core/IO boundary is enforced by an AST test
   (`tests/architecture/test_core_purity.py`) instead of by packaging, which is
   stronger and has no install friction. `services/api` and `services/worker`
   remain as deployment units.
2. **SQLite + in-process bus fallbacks** so the demo runs with no Docker. Both
   are loudly reported, never silent.
3. **VORP uses a static pre-draft baseline.** Draft dynamics are modelled by the
   separate scarcity and survival terms; a dynamic baseline would double-count.
4. **Additive, decomposable scoring.** Explainability outranks marginal accuracy
   under a draft clock.
5. **Two-pass ranking** resolves the `adp_value`/`model_rank` circularity.
6. **`player_uuid` is UUIDv5 anchored on `gsis_id`**, so it is stable across
   re-ingestion without a DB round-trip.
7. **Python 3.14** (only 3.12+ available); `requires-python = ">=3.12"`.

---

## 7. Conventions to keep

- Every constant named and justified; no inline magic numbers.
- Every score decomposable; components must reconcile to the total (there are
  tests asserting exactly this).
- Missing data lowers **confidence**; it never invents risk or a value.
- Never discard raw provider text.
- No bare `except` (enforced by a test); no `print` (ruff `T20`).
- Language: "availability risk", never "will get injured" (directive §43).
- Tests must never depend on the network. Live tests go behind the `live` marker.
