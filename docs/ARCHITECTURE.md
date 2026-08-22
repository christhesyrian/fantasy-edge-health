# Architecture

How the system is put together, and the reasoning behind the shape.

---

## Layers

```
                        ┌─────────────────────────────────────┐
  Sleeper API ─────────►│  fhe.data.providers                 │
  nflverse releases ───►│  typed · retried · rate-limited      │
  DynastyProcess ──────►│  contract-tested against fixtures    │
  CSV upload ──────────►└──────────────┬──────────────────────┘
                                       │
                        ┌──────────────▼──────────────────────┐
                        │  fhe.data.identity + fhe.data.ingest │
                        │  UUIDv5 resolution · lineage ·       │
                        │  quality gates · conflicts recorded  │
                        └──────────────┬──────────────────────┘
                                       │
                        ┌──────────────▼──────────────────────┐
                        │  fhe.db — PostgreSQL, 26 tables      │
                        │  provenance on every observation     │
                        └──────────────┬──────────────────────┘
                                       │
   ┌───────────────────────────────────▼──────────────────────┐
   │  fhe.core — PURE DOMAIN, zero I/O                        │
   │  injury taxonomy · health model · draft engine · sim     │
   └───────────────────────────────────┬──────────────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
    ┌──────────────────┐   ┌────────────────────┐   ┌────────────────┐
    │ fhe.api          │   │ fhe.worker         │   │ fhe.cli        │
    │ FastAPI · SSE    │   │ live draft poller  │   │ ingest · sim   │
    └────────┬─────────┘   └────────────────────┘   └────────────────┘
             │
             ▼
    ┌──────────────────┐
    │ apps/web         │
    │ renders only     │
    └──────────────────┘
```

---

## The central constraint: a pure domain

Everything in `src/fhe/core/` is free of I/O. No database, no HTTP, no
filesystem, no clock that is not injected.

This is not aesthetic. It buys four specific things:

1. **The engine is testable without infrastructure.** 411 tests run in 16
   seconds with no database and no network.
2. **The simulator is a real rehearsal.** The mock draft and the live Sleeper
   draft drive the *same* code, so a bug found in simulation is a bug in
   production.
3. **Reasoning is local.** Understanding why a player is ranked where they are
   requires reading one module, not tracing through a query.
4. **Recommendations are reproducible.** Same input, same output, always — which
   is what makes a seeded simulation a regression test.

Enforced by [`tests/architecture/test_core_purity.py`](../tests/architecture/test_core_purity.py),
which walks the AST of every core module and fails on a forbidden import.
Packaging cannot enforce this within one distribution, so a test does.

---

## Deliberate deviations from the brief's layout

The directive suggested separate packages under `services/` and `packages/`.
This uses a **single Python distribution with `src/fhe/`** instead:

- The core/I/O boundary is enforced by an AST test, which is *stronger* than
  package boundaries and has no install friction.
- One dependency set, one virtualenv, one mypy run.
- `services/api/` and `services/worker/` remain as deployment units with their
  own Dockerfiles, which is what they were actually for.

Recorded as [ADR 0001](adr/0001-single-python-distribution.md).

---

## Request paths

### Board recompute (the hot path)

```
GET /api/v1/simulations/{id}/board
  → registry.get(session)                  in-memory
  → evaluate_draft(state, pool, baseline)  pure, ~10ms
      ├ replacement baseline               precomputed at session creation
      ├ scarcity + tiers                   over available players
      ├ roster need                        from the user's picks
      ├ two-pass scoring                   provisional rank, then ADP value
      └ board assembly                     headline picks + alerts
  → map to wire types
```

Measured over a 324-player pool: the engine itself runs at a **9.3 ms median,
10.0 ms p95**; end to end through the API, including serialisation, the war room
reports 10–17 ms. The baseline is computed once per session because, by design,
it does not change during a draft.

### Live pick ingestion

```
poller → provider.get_draft_picks()        every ~3s, adaptive
       → DraftState.apply_picks()          sorts, dedupes, detects conflicts
       → event bus publish                 pick_made, then board_updated
       → SSE → browser
       → browser refetches /board          canonical state, never a local patch
```

The browser deliberately does **not** patch a local board from events. A board
that has drifted from the engine's answer is worse than one that lags by 200ms,
because the manager cannot tell which they are looking at.

---

## Real-time transport

Server-sent events, not WebSockets. Draft updates are overwhelmingly
server-to-client; SSE reconnects automatically in the browser with no library
and survives proxies that mangle upgrades. The one thing it cannot do —
client-to-server streaming — is not needed, because user actions are ordinary
POSTs.

**Delivery is not guaranteed, and the design says so.** Neither bus is a durable
queue. Every event carries a monotonic per-draft sequence so a client can
*detect* a gap, and the response is always to re-read canonical state rather
than reconstruct what was missed.

Two implementations behind one interface: an in-process bus (zero
infrastructure, single process only) and Redis pub/sub. The health endpoint
reports which is active.

### A subtle bug worth recording

`subscribe()` was originally an async generator. A generator's body does not run
until its first `__anext__`, so the queue was registered *after* the handler
returned — and every event published in that window was silently lost, which
during a draft is exactly when picks arrive. Registration is now eager and
completes before `subscribe()` returns.

---

## Degradation strategy

The product runs with no PostgreSQL, no Redis, no credentials, and no ingested
data. Each fallback is deliberate, and each announces itself.

| Missing | Fallback | Consequence |
| --- | --- | --- |
| `FHE_DATABASE_URL` | SQLite file | Single process only |
| `FHE_REDIS_URL` | In-process event bus | Events do not cross processes |
| Ingested data | Synthetic demo pool | Labelled synthetic everywhere |
| Player crosswalk | Direct id matching only | nflverse linkage drops ~97% → ~21% |
| Sleeper connection | Demo mode | Live features shown as unavailable |

`Settings.storage_warnings()` surfaces every active degradation at startup, on
`/api/v1/health`, and in the onboarding UI. A degraded configuration can never
be mistaken for a production one.

---

## Failure handling at the boundary

Providers fail, and draft night is the worst time for it.

- **Rate limiting is self-imposed and conservative.** 600 req/min against a
  documented 1000 ceiling, because an IP block is unrecoverable within a session.
- **Retries use full jitter.** Exponential backoff without jitter synchronises
  every client into one retry wave.
- **Only transient failures retry.** A 404 is an answer, not a wobble.
- **A malformed response raises rather than coercing.** It must never overwrite
  known-good state.
- **An empty response is never a reset.** A poller that read zero picks leaves
  the board exactly as it was.
- **Not-found semantics are per-endpoint.** A missing league returns `None`
  because that is a normal onboarding outcome; missing draft picks *raise*,
  because "this draft vanished" and "nobody has picked yet" must stay
  distinguishable.

---

## Identity resolution

The hardest data problem here, and the one with the most measurement behind it.

Sleeper publishes `gsis_id` for **21%** of top-200 fantasy players. nflverse
publishes no `sleeper_id`. So the two cannot be joined directly for four fifths
of the pool that matters.

Resolution is layered by evidence strength:

1. Direct `gsis_id` from the Sleeper payload — confidence 1.0
2. DynastyProcess crosswalk — 0.97
3. Normalised name + team + position — 0.85
4. Name + position alone — 0.60, **below the auto-accept threshold**, so it
   becomes a conflict record rather than a silent match

Measured: **100%** of the top 200 resolve with zero conflicts; 97.1% across all
1,038 rostered skill players.

`player_uuid` is a deterministic UUIDv5 anchored on `gsis_id`, so it is stable
across re-ingestion and never requires a database round trip to allocate.

---

## Observability

Structured logs from the start rather than retrofitted, with a redaction
processor that strips credential-shaped keys before rendering. Every request
carries a correlation id, honouring an inbound `x-request-id` so a trace
survives a proxy hop.

Prometheus metrics cover provider requests and latency, poll outcomes, pick
ingestion by disposition, recommendation latency, ingestion rows by disposition,
and active SSE clients. Nothing requires a paid service to run locally.

---

## Testing strategy

| Layer | What it proves |
| --- | --- |
| Unit | Domain behaviour without infrastructure |
| Contract | Our parsing of saved provider fixtures |
| Architecture | The purity boundary and structural rules |
| Integration | Schema, ingestion, and migrations against a real database |
| API | HTTP semantics, including SSE against a real server |
| Frontend | Component behaviour and the reconnect/gap logic |

The default suite never touches the network. Live-provider tests sit behind a
marker, so a pull request is never gated on someone else's uptime.

SSE is tested against a real uvicorn server rather than the ASGI transport,
because httpx's transport never delivers `http.disconnect` — a streaming
response never completes under it, and the read hangs.
