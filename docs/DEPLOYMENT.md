# Deployment

How Fantasy Health Edge is meant to run in production, and why it is not one
deployable.

The short version: **the frontend deploys to Vercel, the backend does not.**
The API owns long-lived server-sent event streams and a polling loop that must
keep running between requests, and neither survives a request-scoped serverless
model. Pretending otherwise would produce a war room that silently stops
updating mid-draft.

---

## 1. Topology

```
                       ┌──────────────────────────┐
   browser ──────────► │  Next.js (Vercel)        │
                       │  static + RSC, no state  │
                       └───────────┬──────────────┘
                                   │  HTTPS + SSE, cross-origin
                                   ▼
                       ┌──────────────────────────┐
                       │  FastAPI (container)     │
                       │  • REST reads            │
                       │  • SSE streams           │
                       │  • live draft poller     │
                       │  • in-memory sessions    │
                       └────┬────────────────┬────┘
                            │                │
                  ┌─────────▼──────┐  ┌──────▼─────────┐
                  │  PostgreSQL    │  │  Redis         │
                  │  (managed)     │  │  (optional)    │
                  └────────────────┘  └────────────────┘
                            │
                  ┌─────────▼──────────────────────────┐
                  │  Object storage (optional)         │
                  │  raw ingestion artefacts           │
                  └────────────────────────────────────┘
```

**The frontend deploys independently of the backend.** It is a static build
plus RSC with no database access and no server-side secrets; its only coupling
to the API is `NEXT_PUBLIC_API_BASE_URL`. Shipping a UI change requires no API
deploy, and vice versa.

## 2. Component placement, and the reasoning

| Component | Where | Why not elsewhere |
| --- | --- | --- |
| Next.js web | Vercel, or any Node host | Nothing here needs a long-lived process. |
| FastAPI + poller | A container platform that runs **persistent** processes — Fly.io, Railway, Render, ECS/Cloud Run with min-instances ≥ 1 | Serverless functions are billed and killed per request. An SSE stream is a request that lasts the whole draft, and the poller must tick while nobody is asking for anything. |
| PostgreSQL | Managed (Neon, Supabase, RDS, Cloud SQL) | Running a database beside the app trades an hour of setup for a permanent operational burden. |
| Redis | Managed, and **only when running more than one API worker** | With one worker the in-process bus is correct and simpler. See §7. |
| Object storage | Managed S3-compatible, optional | Only stores raw provider artefacts for re-normalisation. The product works without it. |

Provider names above are illustrative. Nothing in the codebase is coupled to a
vendor: the API is a container with a health check, the database is reached by
URL, and the cache is optional.

## 3. Why the API cannot be serverless

Three concrete reasons, each of which is on its own sufficient:

1. **Server-sent events.** `/api/v1/drafts/{id}/events` holds a connection open
   for the length of a draft, sending a heartbeat every 15 seconds. Platform
   function timeouts are measured in seconds to minutes.
2. **The live draft poller.** `PollerManager` runs a background task per
   connected draft, polling Sleeper on an adaptive interval. It must run when
   no HTTP request is in flight, which a request-scoped runtime cannot do.
3. **In-memory draft sessions.** A session holds the evaluated board and the
   player pool. Rebuilding it per invocation would make every request pay the
   connect cost. (Sessions *can* be rebuilt after a restart — see §7 — but that
   is a recovery path, not a per-request one.)

## 4. Environment variables

Full templates: [`.env.example`](../.env.example) (backend) and
[`apps/web/.env.example`](../apps/web/.env.example) (frontend). They are
separate files on purpose, so a frontend deployment is never asked for a
database URL.

### Frontend — set in the Vercel project

| Variable | Required | Exposure | Value |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | Production: **yes** | **Public** — inlined into the browser bundle | `https://api.your-domain.example` (no trailing slash). Defaults to `http://localhost:8000` if unset, which is wrong for a deployed site. |
| `NEXT_PUBLIC_PREVIEW_MODE` | No | Public | Unset in production. `fixtures` enables the offline preview described in [`V0_HANDOFF.md`](V0_HANDOFF.md). |

Never introduce a secret behind `NEXT_PUBLIC_`. It is not a convention, it is a
compile step: the value is written into JavaScript every visitor downloads.

### Backend — set on the container platform

| Variable | Required | Notes |
| --- | --- | --- |
| `FHE_ENV` | Yes | `production`. Drives logging and error verbosity. |
| `FHE_LOG_FORMAT` | Yes | `json` in production, so logs are queryable. |
| `FHE_LOG_LEVEL` | No | `INFO`. |
| `FHE_DATABASE_URL` | **Yes in production** | A managed platform's own connection string works as-is: Render, Railway, Heroku and Fly all emit `postgres://`, which SQLAlchemy 2 rejects and which names no driver, so it is normalised to `postgresql+asyncpg://` on read. An explicitly chosen driver is respected. If unset the app falls back to a local SQLite file and *says so* on `/health` — fine for a demo, wrong for a deployment. |
| `FHE_REDIS_URL` | Only for >1 worker | Unset means an in-process event bus, which is correct for a single worker and broken for several. |
| `FHE_ACCESS_PASSWORD` | **Yes in production** | The shared password. Production with this unset **refuses to start**, because an open instance fails silently: everything works while serving every draft and import to anyone with the URL. `openssl rand -base64 24` generates one. |
| `FHE_ACCESS_SESSION_HOURS` | No | Defaults to 72. How long a signed-in browser stays signed in. |
| `FHE_ACCESS_MAX_ATTEMPTS` / `FHE_ACCESS_LOCKOUT_MINUTES` | No | Defaults 10 and 15. Failed sign-ins per client address before a lockout. Counted per process, so several workers multiply the allowance. |
| `FHE_CORS_ORIGINS` | Yes | Comma-separated **exact** origins, e.g. `https://app.your-domain.example`. Never `*`. |
| `FHE_API_HOST` / `FHE_API_PORT` | Platform-dependent | Bind `0.0.0.0` inside a container. |
| `FHE_SLEEPER_MAX_RPM` | No | Defaults to 600, under Sleeper's documented 1000/min. |
| `FHE_DRAFT_POLL_INTERVAL_SECONDS` | No | Defaults to 3.0. |
| `FHE_S3_*` | No | Only for raw artefact retention. |
| `FHE_ANTHROPIC_API_KEY` | No | Optional assistant only. The product is fully usable without it, by design. |

No provider credential is required for the core product: Sleeper and nflverse
are both public and unauthenticated.

## 5. CORS and SSE

The browser calls the API cross-origin, so both matter and both have sharp
edges:

- **`FHE_CORS_ORIGINS` must list exact origins.** Preview deployments get
  generated hostnames, so either add them explicitly or point previews at a
  backend configured for them. A wildcard with credentials enabled is rejected
  by browsers anyway.
- **Proxies buffer.** Any CDN, load balancer, or ingress between the browser
  and the API must not buffer `text/event-stream`, or events arrive in bursts
  when a buffer flushes rather than when picks happen. Disable response
  buffering on that route.
- **Idle timeouts must exceed the heartbeat.** The server sends a keep-alive
  every 15 s specifically so an idle draft does not look dead; set any proxy
  idle timeout above that, and the client's `STALE_AFTER_MS` (40 s) is the
  budget it allows for a missed beat.
- **HTTP/1.1 connection limits.** A browser allows six connections per origin
  over HTTP/1.1, and an SSE stream holds one for the whole draft. Serve the API
  over HTTP/2 if several tabs are expected.

## 6. Health checks and lifecycle

| Endpoint | Use |
| --- | --- |
| `GET /api/v1/health` | **Liveness.** Cheap, no dependencies. |
| `GET /api/v1/health/ready` | **Readiness.** Checks the database and reports degradations. |
| `GET /api/v1/metrics` | Prometheus scrape. |

Both health endpoints report **degradations by name** — SQLite instead of
Postgres, in-process bus instead of Redis. A deployment that believes it is
production while running on fallbacks will say so out loud rather than look
healthy. Alert on a non-empty `degradations` array in production.

Shutdown cancels every poller task (`PollerManager.stop_all`), so a rolling
deploy does not leak polling loops against Sleeper.

## 7. Workers, sessions, and what Redis is actually for

Draft sessions live in the API process's memory. That has two consequences,
and only one of them is a problem:

**Restarts are handled.** A live Sleeper draft is reconstructable from facts
that survive a restart — the persisted league and draft rows, the provider's
current picks, the canonical player pool, and a deterministic engine. Any read
of a draft whose session is missing rebuilds it and restarts its poller; see
`src/fhe/api/services/session_recovery.py`. Reloading the war room after an API
restart mid-draft returns a board, not a 404. Mock simulations are deliberately
*not* recovered: they are seeded, ephemeral, and cost a keystroke to recreate.

**Multiple workers are not.** Two workers would each hold their own copy of a
session and their own poller, double-polling the provider and disagreeing about
sequence numbers. So:

- **Run one API worker** unless you have done the work below. This is the
  supported configuration, and §4 of [`PERFORMANCE.md`](PERFORMANCE.md) shows
  one worker sustains roughly 90 board evaluations per second — far beyond what
  a draft produces.
- **To run several**, you need `FHE_REDIS_URL` for the shared event bus *and*
  either sticky sessions routed by draft id or a lock ensuring one poller per
  draft across the fleet. The event bus is already abstracted for this; the
  poller ownership is not, and that is the honest remaining work.

Scale vertically first. This is a product where one process is genuinely
enough, and distributing it would add failure modes to buy throughput nobody
needs.

## 8. Database migrations

Alembic owns the schema. A test asserts the models and migrations have not
drifted, which catches the classic failure of a model changed without a
revision.

```bash
./.venv/bin/alembic upgrade head
```

Run migrations **as a separate step before the new API starts**, not from
application startup: startup migration means every replica races to migrate,
and a failed migration becomes a crash loop instead of a failed job. The
Compose stack models this with a dedicated `migrate` service that the API
depends on.

Migrations are expand-only in practice — add columns and tables, backfill,
then remove in a later release — so an old and a new API version can run
against the same schema during a rollout.

## 9. Deploying to Render, concretely

[`render.yaml`](../render.yaml) declares the API, its PostgreSQL database, and a
Key Value instance for the event bus. Schema verified against Render's blueprint
spec on 2026-09-04.

1. **Push this repository to GitHub**, if it is not there already.
2. On Render: **New → Blueprint**, select the repository. It reads `render.yaml`
   and offers to create three resources.
3. It prompts for the variables marked `sync: false`. Leave `FHE_CORS_ORIGINS`
   as a placeholder for now — the Vercel URL does not exist yet — and skip
   `FHE_FANTASYPROS_API_KEY` unless the licence is yours to honour for every
   user of the instance.
4. **Read the generated password**: Render → `fhe-api` → Environment →
   `FHE_ACCESS_PASSWORD`. That is what you give the people who should get in.
5. Wait for the first deploy. `preDeployCommand` runs `alembic upgrade head`, so
   the schema is created before the API takes traffic.
6. **Seed the database** from Render → `fhe-api` → Shell, in this order. Nothing
   works before this: the pool is empty and a live draft refuses to connect.

   ```
   fhe ingest players
   fhe ingest injuries
   fhe ingest workload
   fhe ingest schedule
   fhe ingest depth-charts
   ```

7. **Deploy the frontend to Vercel** with `NEXT_PUBLIC_API_BASE_URL` set to the
   Render URL (`https://fhe-api-xxxx.onrender.com`, no trailing slash).
8. **Close the loop**: set `FHE_CORS_ORIGINS` on Render to the exact Vercel
   origin. Until these two point at each other, the browser refuses every
   request and the site looks broken while the API is perfectly healthy.
9. **Verify**: load the site, enter the password, start a demo draft.

The container reads `$PORT`, which Render assigns, via
[`services/api/entrypoint.sh`](../services/api/entrypoint.sh). That script uses
`exec` so uvicorn is PID 1 and receives `SIGTERM` directly — without it a
redeploy would kill a live poller mid-cycle instead of shutting it down through
the lifespan.

### Railway instead

Railway has no blueprint file, so it is configured in its UI, but nothing else
differs: point it at `services/api/Dockerfile`, add PostgreSQL and Redis
plugins, set the same variables, and use `alembic upgrade head` as the
pre-deploy command. `$PORT` and `postgres://` are both handled the same way.

## 10. Deployment order

Backend first, because the frontend calls it and an old frontend against a new
backend is the safer overlap.

1. **Provision** PostgreSQL and, if running more than one worker, Redis.
2. **Migrate**: `alembic upgrade head` as a one-shot job against the new image.
3. **Deploy the API**, wait for `/api/v1/health/ready` to report ready with an
   empty `degradations` array. A non-empty one names exactly what is missing —
   an unset password, no Redis, the SQLite fallback — in plain language.
4. **Ingest**, on a first deploy only: `fhe ingest players`, then
   `fhe ingest injuries`, then `fhe ingest workload`. Without this the database
   has no players and live drafts refuse to connect with a clear error.
5. **Deploy the frontend** with `NEXT_PUBLIC_API_BASE_URL` pointing at the API.
6. **Verify**: load the site, start a demo draft, press `a`, confirm the feed
   indicator reads `LIVE`.

## 11. Rollback

- **Frontend**: redeploy the previous build. It is stateless, so this is
  instant and always safe.
- **API**: redeploy the previous image. In-memory sessions are lost, but live
  drafts rebuild themselves on the next read (§7), so the user-visible cost of
  an API rollback mid-draft is one slow request.
- **Database**: prefer rolling *forward*. Because migrations are expand-only,
  the previous API version runs against the newer schema, so an API rollback
  rarely needs a schema rollback. `alembic downgrade -1` exists but destroys
  whatever the upgrade added.
- **Ingestion**: re-running is safe. Every job is idempotent and converges, and
  plausibility floors mean a corrupt provider response is refused rather than
  written over good data.

## 12. Local production-shaped stack

`docker-compose.yml` brings up PostgreSQL, Redis, MinIO, migrations, the API,
and the web app together. It is the closest local equivalent to the topology
above and the right way to test a change against real storage engines.

```bash
docker compose up --build
```

Verified end to end on 2026-08-23: all six services up, `alembic upgrade head`
applied in its own container against PostgreSQL, `/api/v1/health/ready`
reporting an empty `degradations` array, a demo draft evaluated, server-sent
events delivered over Redis with monotonic sequences, ingestion writing 4,089
players, and `docker compose down` shutting everything down cleanly.

Two things that only a real run could have found, both fixed: the frontend
lockfile was out of sync so `npm ci` failed, and the cache volume mounted as
root under a non-root container user, killing ingestion with a
`PermissionError`. The second is worth remembering — Docker seeds an empty
named volume from whatever the image has at that path, ownership included, so
the directory must exist and be owned correctly at build time.
