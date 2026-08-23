# Fantasy Health Edge

**Injury-adjusted fantasy football draft intelligence, with a live draft war room.**

Answers one question, continuously, while your draft is running:

> *Who should I draft right now, after accounting for expected production,
> availability risk, roster construction, positional scarcity, ADP value, and
> the probability this player survives until my next pick?*

```
┌──────────────────────────────────────────────────────────────────────────┐
│  FH  FANTASY HEALTH EDGE          DEMO · SYNTHETIC DATA      ● LIVE  0s   │
├──────────────────────────────────────────────────────────────────────────┤
│  WARNING  Only 1 RB left in this tier — 19 projected points to the next.  │
├───────────────────────────────┬──────────────────────────┬───────────────┤
│  BEST AVAILABLE               │  YOU ARE ON THE CLOCK    │  MY ROSTER    │
│   #  PLAYER      SCORE  RISK  │                          │   QB  —       │
│   1  J. Donnelly  71.0  ▇▇░░  │  JOSIAH DONNELLY   71.0  │   RB  B. Dev… │
│   2  J. Yarboro…  66.3  ▇░░░  │  RB · CHI · bye 13       │   RB  —       │
│   3  Z. Devereaux 64.4  ▇▇▇░  │                          │   WR  —       │
│   4  S. Calloway  63.0  ▇░░░  │  WHY THIS SCORE          │               │
│                               │   +35.9  Value over rep… │  SCARCITY     │
│                               │   +12.0  Positional sca… │   RB ████████ │
│                               │   +12.0  Roster need     │   WR ████░░░░ │
│                               │    +9.0  Next-pick urge… │               │
└───────────────────────────────┴──────────────────────────┴───────────────┘
```

---

## What makes it different

**Availability risk, not injury prediction.** The health model estimates
*fantasy-relevant availability* from public injury reports. It never claims a
player will get hurt, and every health figure ships with its own limitations.

**Every score decomposes.** A `71.0` is never presented alone — the components
that produced it always sum to it, and a test asserts that they do. No opaque
numbers, no LLM prose standing in for reasoning.

**The engine is pure.** All recommendation logic lives in `src/fhe/core/`, has
zero I/O, and is driven identically by the live Sleeper draft, the mock
simulator, and the test suite. A rehearsal is a real rehearsal.

**Honest about data.** Every metric carries its provider and timestamp. Missing
data lowers confidence rather than inventing a value; an unmeasured player is
*unknown*, not *safe*. Degraded configurations announce themselves.

---

## Quick start — demo mode, no credentials

Requires Python 3.12+ and Node 20+. No database, no Docker, no accounts.

```bash
make setup          # virtualenv, Python deps, npm deps
make dev-api        # terminal one  → http://localhost:8000
make dev-web        # terminal two  → http://localhost:3000
```

Open <http://localhost:3000>, choose your league shape, and enter the war room.
Press `a` to run the draft to your pick.

Prefer the whole stack in containers:

```bash
docker compose up --build
```

No browser needed at all:

```bash
./.venv/bin/python -m fhe.cli simulate --seed 42
```

---

## Features

### Live draft war room
Connect a Sleeper draft, or run a seeded mock. Picks arrive over server-sent
events; drafted players leave the board, rosters fill, scarcity shifts, and
recommendations recompute in single-digit milliseconds. Connection state is
always visible as `LIVE`, `RECONNECTING`, or `STALE`.

### Availability risk
Every player carries a 0–100 risk score built from injury designation, practice
trajectory, injury recency, same-region recurrence, games missed, a
position-specific ageing curve, and workload — each contribution itemised and
explained in words.

### Injury-adjusted rankings
Value over replacement against a league-specific baseline, positional scarcity,
tier cliffs, roster need, ADP value, and a health adjustment, combined additively
so the arithmetic is always visible.

### Next-pick survival probability
The flagship signal: the probability a player is still available when you next
pick, conditional on them being available now, and re-anchored when they have
fallen past their ADP.

### Player detail and comparison
A drawer with the full risk breakdown, an injury timeline that preserves the
provider's original wording, usage charts, and data provenance. Two to four
players compare side by side without leaving the board.

### Mock draft simulator
Seeded and deterministic. The same seed reproduces the same draft exactly, which
makes it a regression test as much as a demo.

---

## Architecture

```
Sleeper API ─┐                                   ┌─ Next.js war room
             ├─→ providers ─→ identity ─→ Postgres ─→ FastAPI ─→ SSE ─┤
nflverse ────┤    (typed,     resolution   (26      (pure engine)     └─ CLI
             │     retried,   (UUIDv5)     tables)
CSV import ──┘     rate-      conflicts →
                   limited)   recorded
```

| Layer | Location | Rule |
| --- | --- | --- |
| Domain | `src/fhe/core/` | **Pure.** No I/O, enforced by an AST test. |
| Data | `src/fhe/data/` | Providers, ingestion, identity resolution. |
| Persistence | `src/fhe/db/` | SQLAlchemy 2 + Alembic, 26 tables. |
| API | `src/fhe/api/` | FastAPI, SSE, transport concerns only. |
| Worker | `src/fhe/worker/` | Live draft poller. |
| Web | `apps/web/` | Next.js 16. Renders; computes nothing. |

Full detail in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Technologies

**Backend** Python 3.12+ · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) ·
Alembic · PostgreSQL · Redis · httpx · Polars · structlog · Prometheus

**Frontend** Next.js 16 · React 19 · TypeScript strict · Tailwind v4 ·
TanStack Query · Zod · Vitest · Testing Library

**Tooling** Ruff · mypy strict · pytest · Docker · GitHub Actions · pre-commit

## Data sources

Every source is verified against official documentation **and** live responses
before use. See [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) for the full
record, including where documentation and reality disagree.

| Source | Use | Auth |
| --- | --- | --- |
| [Sleeper](https://docs.sleeper.com) | Leagues, drafts, rosters, players, live picks | None |
| [nflverse](https://github.com/nflverse/nflverse-data) | Injury history 2009–2025, stats, snaps, depth charts | None |
| [DynastyProcess](https://github.com/dynastyprocess/data) | Player id crosswalk (GPL-3.0, fetched at runtime, never redistributed) | None |
| CSV import | ADP and projections you are licensed to use | — |

No provider is scraped, and no integration is faked. Providers that are not
built are shown as unavailable rather than as buttons that fail.

## Testing

```bash
make test          # everything
make test-py       # Python
make test-web      # frontend
make test-live     # opt-in, hits real providers
```

The default suite never touches the network. Provider behaviour is pinned by
contract tests against saved fixtures, and the live tests sit behind a marker so
a pull request is never gated on someone else's uptime.

Notable coverage: the injury taxonomy is validated against 62,915 real
observations; the draft poller is tested against provider outages, duplicate
responses, out-of-order arrival, and conflicting picks; SSE is exercised against
a real HTTP server because the ASGI transport cannot reproduce disconnects.

## Machine learning

A transparent heuristic runs in production and is always the fallback. A learned
availability model is **built, audited, and evaluated — and deliberately not
promoted**.

```bash
./.venv/bin/python -m fhe.cli ml evaluate
```

That builds a 58,202-row point-in-time dataset across ten seasons, runs a
seven-check leakage audit, and evaluates candidates against two baselines on
held-out seasons. The audit's strongest check rebuilds the dataset with later
weeks removed and asserts the earlier rows are identical — a structural proof
that no feature reached forward.

The result worth reading is in [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md): the
best-*ranking* model has a Brier score twice as bad as predicting the base rate,
because reweighting the loss bought ranking and destroyed calibration. Since the
product shows this number as a probability, the promotion bar requires
calibration as well as ranking — and so does model selection, because choosing on
ROC-AUC alone repeats the same mistake.

## Documentation

| Document | Contents |
| --- | --- |
| [`HANDOFF.md`](HANDOFF.md) | Current state, decisions, and the ordered backlog |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design and data flow |
| [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) | Every source, verified, with its limits |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Schema and why it is shaped that way |
| [`docs/DRAFT_ENGINE.md`](docs/DRAFT_ENGINE.md) | The recommendation mathematics |
| [`docs/INJURY_MODEL.md`](docs/INJURY_MODEL.md) | Health features and limitations |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Operating it, including on draft night |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model and decisions |
| [`docs/INTERVIEW_GUIDE.md`](docs/INTERVIEW_GUIDE.md) | Design decisions and trade-offs |
| [`docs/adr/`](docs/adr/) | Architecture decision records |

## Limitations

Stated plainly, because a portfolio project that hides its edges is less useful
than one that names them:

- **Availability risk is not a medical model.** It uses public injury reports,
  which omit severity and prognosis entirely.
- **The heuristic model is not validated against outcomes.** Its weights are
  reasoned, not fitted. That is why it is labelled heuristic.
- **Demo data is synthetic.** Realistic in shape, invented in substance, and
  labelled as such everywhere it appears.
- **Draft sessions live in memory.** They do not survive an API restart and do
  not span processes. Redis is required before running more than one.
- **Survival probability assumes a normal draft-position distribution.** Real
  distributions are right-skewed; the model re-anchors fallers to compensate,
  which is a correction, not a fix.
- **No ADP or projections ship with the product.** Bring your own by CSV.

## Roadmap

1. Persist live Sleeper drafts and wire the poller into the war room UI
2. Weekly stats and snap-count ingestion for workload features
3. Validated availability model with a calibration report and model card
4. Playwright end-to-end coverage of the full demo path
5. Command palette, favourites, and saved league configurations

## License

MIT — see [`LICENSE`](LICENSE).
