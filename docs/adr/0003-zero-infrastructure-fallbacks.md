# 3. SQLite and in-process bus fallbacks

**Status:** Accepted · 2026-08-22

## Context

The acceptance criteria require someone to clone the repository and reach demo
mode. Requiring PostgreSQL, Redis, and Docker first puts three installation
failures between a reviewer and the product.

Docker was not even running on the development machine when this was decided,
which made the problem concrete rather than hypothetical.

## Decision

Without `FHE_DATABASE_URL`, fall back to a local SQLite file. Without
`FHE_REDIS_URL`, fall back to an in-process event bus. Both are reported by
`Settings.storage_warnings()` at startup, on `/api/v1/health`, and in the
onboarding UI.

## Alternatives considered

**Require Docker.** Rejected: it turns "see the product" into "debug my Docker
install", and the Docker daemon genuinely was not running here.

**Silent fallback.** Rejected outright. A fallback nobody knows about is how a
production deployment ends up on SQLite. Reporting it is what makes the fallback
acceptable rather than dangerous.

**SQLite only.** Rejected: JSONB, native UUID, and concurrent writes matter, and
designing around SQLite's limits would compromise the real target.

## Consequences

**Good.** `make dev-api` works immediately. Tests run against SQLite in
milliseconds with no service orchestration. The degradation list doubles as
onboarding documentation.

**Bad.** Two dialects to support, handled by `with_variant` in
`fhe/db/base.py` — which is real complexity. SQLite's `NULL != NULL` behaviour in
unique constraints caused a genuine bug (see ADR-adjacent note in
`docs/DATA_MODEL.md`), though PostgreSQL behaves identically there.

CI runs the suite on both engines, because a fallback that is never tested on
the real target is a fallback that breaks silently.
