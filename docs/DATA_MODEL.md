# Data model

26 tables, and the reasoning behind the shape.

Definitions: [`src/fhe/db/models/`](../src/fhe/db/models/). Migrations:
[`alembic/versions/`](../alembic/versions/).

---

## Principles

**Provenance is a column, not an afterthought.** Nearly every table carries
`source`, `source_updated_at`, `ingested_at`, and `observed_at`. The product
promises that any number on screen can say where it came from and how old it is,
which is only possible if provenance is stored per observation.

`observed_at` deserves particular attention: it is *when the fact was true*, not
when we read it. That distinction is what makes point-in-time feature
reconstruction possible, and it is the difference between a leak-free training
set and one that quietly knows the future.

**Uniqueness constraints are the idempotency guarantee.** Every ingestion is an
upsert against a natural key. The database enforces it, so two workers racing on
the same payload cannot double-write.

**Raw text is never discarded.** Normalised values sit beside the provider's
original string, so a taxonomy bug is fixable by re-running normalisation rather
than re-ingesting.

**JSONB only where a payload genuinely needs preserving.** Anything queried or
filtered gets a real column.

---

## Core identity

### `players`
One row per player, keyed by `player_uuid` — a deterministic UUIDv5 derived from
the strongest available identifier. Deterministic derivation means re-ingestion
never creates a duplicate and never re-keys a player, and no database round trip
is needed to allocate a key.

Carries `identity_method` and `identity_confidence`, so a consumer can always
distinguish a certain match from a probable one.

### `player_external_ids`
Provider identifiers as rows rather than columns, so a new provider needs no
migration. Two unique constraints: `(system, external_id)` and
`(player_uuid, system)`.

The first is load-bearing and caught a real upstream bug — the crosswalk
contains a handful of ids claimed by two different players. An external
identifier that does not identify exactly one player is useless as a join key,
so ingestion resolves the collision by confidence and reports the loser.

### `player_identity_conflicts`
Players the resolver could not identify confidently. Written instead of
guessing. A player the system cannot recognise is a *known* unknown, and the
diagnostics endpoint surfaces the count so drift is impossible to miss.

### `teams`, `seasons`
Reference data, including bye weeks and current-season state.

---

## Health

### `injury_events`
One row per injury report observation. Unique on
`(player_uuid, season, week, source)`, which makes re-ingesting a season a no-op.

Stores `body_region` **and** `raw_primary_injury`, plus the secondary of each.

### `practice_reports`
Deliberately a separate table from injury events. They answer different
questions: "Questionable after three full practices" and "Questionable after
three DNPs" are the same designation and very different signals, and collapsing
them into one row would make that distinction unqueryable.

### `current_player_health`
One row per player, holding the latest known status. Separate from the event log
because the war room reads it on every board recompute, and that must be a
single indexed lookup rather than an aggregate over a growing history.

### `health_score_snapshots`
Computed risk scores with their component breakdown persisted as JSON. Storing
the components means a score shown during a draft can be explained afterwards
*exactly as it was displayed*, even after the model version changes.

### `availability_predictions`
Reserved for a validated ML model. Separate from heuristic snapshots because the
two have different trust levels and different promotion rules. `horizon_weeks` is
explicit so a prediction can never be read as a different claim than the one it
was trained for.

---

## Football

`player_weekly_stats` carries usage (carries, targets, snaps) and production,
plus pre-computed fantasy points for the three common formats — the war room
must not recompute scoring across thousands of rows per request.

`snap_counts` and `depth_chart_snapshots` complete the workload picture.

---

## Fantasy

### `fantasy_projections`
Multiple providers coexist for the same player and period; the ranking layer
chooses and always reports which it used.

### `adp_snapshots`
A time series, not a mutable value, because ADP moves daily. The war room reads
the most recent and displays its timestamp; a stale ADP is shown as stale rather
than silently trusted.

### `fantasy_rankings`
Rankings are league-specific — replacement level differs between a 10-team
single-QB league and a 12-team superflex, so a single global ranking would be
wrong for nearly everyone. Keyed by `roster_signature`, a hash of the lineup
configuration, so two identically-shaped leagues share a computation.

---

## Draft

`fantasy_leagues` → `drafts` → `draft_picks`, with `draft_slots`,
`fantasy_rosters`, and `roster_players` alongside.

### `draft_picks` — the important one

Two unique constraints:

- `(draft_id, pick_no)` — a draft has exactly one pick at each number
- `(draft_id, player_uuid)` — a player is drafted at most once

These are the **database-level** guarantee behind the idempotency the poller
relies on. Even if two workers process the same provider response
simultaneously, a pick cannot be stored twice; the second insert fails, and that
is the correct outcome.

`draft_slot` and `roster_id` are both stored rather than one being derived from
the other, because they diverge when a pick has been traded.

`provider_player_id` is retained even when identity resolution fails, so a pick
is never lost because we could not recognise the player.

### `draft_recommendation_snapshots`
The board as it was shown at a specific pick. Persisted so a draft can be
reviewed afterwards — what did the system recommend at pick 29, and why —
which is impossible to reconstruct later from data that has since changed.

---

## Data engineering

`data_ingestion_runs` records every run's lineage: provider, dataset, status,
timings, and row counts split by disposition. Rejected rows are counted
separately from processed rows, because a run that silently drops 40% of its
input must never look like a success. A run that read *zero* rows is recorded as
failed, not successful — an empty payload is the condition most likely to wipe
good data unnoticed.

`data_quality_results` holds automated check outcomes.
`provider_sync_state` holds cursors and backoff state, so a provider that is
failing is not hammered on restart.

---

## A bug worth documenting

`week` is `NOT NULL` with a `SEASON_LONG_WEEK = 0` sentinel across
`fantasy_projections`, `injury_events`, `practice_reports`, and
`availability_predictions`.

The reason: **SQL treats `NULL` as never equal to `NULL`**, so a unique
constraint containing a nullable column silently fails to deduplicate. Two
season-long projections for the same player both satisfied
`UNIQUE(player_uuid, season, week, ...)` and `ON CONFLICT` never fired — so
re-importing duplicated every row.

Partial or expression indexes could express this, but `ON CONFLICT` cannot
target them portably across PostgreSQL and SQLite. An explicit sentinel keeps
the constraint meaningful on both. A regression test pins it.

---

## Portability

PostgreSQL is the target; SQLite is supported so the demo runs with no
infrastructure. Dialect differences are confined to
[`src/fhe/db/base.py`](../src/fhe/db/base.py): `JSON` becomes `JSONB` and
`String(36)` becomes native `UUID` on PostgreSQL via `with_variant`.

CI runs the suite on both, because JSONB behaviour and stricter typing only
apply on the real engine.
