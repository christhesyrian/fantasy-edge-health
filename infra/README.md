# Infrastructure

## Local

`docker compose up --build` from the repository root brings up PostgreSQL,
Redis, MinIO, migrations, the API, a worker, and the web app. See
[`../docker-compose.yml`](../docker-compose.yml).

The application also runs with none of it — it falls back to a local SQLite file
and an in-process event bus, and says so at startup and on `/api/v1/health`.
That fallback exists so a reviewer can see the product working in one command;
it is not a deployment target.

## Production shape

Nothing here is deployed yet, so this describes the intended shape rather than
an existing environment. It is written down so the containers are not designed
into a corner.

| Component | Target | Why |
| --- | --- | --- |
| Web | Vercel, or any Node host | The Next.js image uses standalone output. `NEXT_PUBLIC_API_BASE_URL` is baked at build time, so a build is environment-specific. |
| API | Any container runtime (ECS, Cloud Run, Fly) | Stateless apart from in-memory draft sessions. Readiness at `/api/v1/health/ready`, liveness at `/api/v1/health`. |
| Worker | Same image, scheduled | Ingestion is a job, not a daemon. Player sync at most daily, per the provider's guidance. |
| Database | Managed PostgreSQL 17 | JSONB and native UUID are used where they help. |
| Cache / bus | Managed Redis | Required as soon as more than one API process runs; without it, draft events do not cross processes. |
| Object storage | S3 or compatible | Immutable raw ingestion artefacts. MinIO locally. |

### Before running more than one API process

Draft sessions are held in memory (see
`src/fhe/api/services/draft_session.py`). With several processes, a request can
land on one that has never heard of a session. Either pin sessions with sticky
routing, or move session state into Redis. Redis is also mandatory at that point
for events to reach every subscriber.

### Scaling notes

- The recommendation engine is pure and CPU-bound; a board recompute is single
  digit milliseconds, so throughput scales with processes.
- The draft poller is one task per active draft at roughly 20 requests per
  minute each. Sleeper's documented ceiling is 1000 per minute, so a single
  worker supports many concurrent drafts — but the limit is per IP, which means
  it is shared across every worker behind the same egress address.
- The player payload is ~15 MB and cached on disk. Give the container a volume,
  or every restart refetches it.
