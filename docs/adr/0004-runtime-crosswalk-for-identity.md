# 4. Runtime GPL crosswalk for identity resolution

**Status:** Accepted · 2026-08-22

## Context

Sleeper and nflverse must be joined per player. Measured against live data:

- Sleeper publishes `gsis_id` for **21%** of top-200 fantasy players
- nflverse publishes **no** `sleeper_id`
- `sportradar_id` is on ~100% of Sleeper players, but nflverse does not publish it

So a direct join strands four fifths of the pool that matters. The
community-maintained DynastyProcess `db_playerids.csv` bridges them — and is
**GPL-3.0**, while this project is MIT.

## Decision

Fetch the crosswalk at runtime into a git-ignored cache. Never commit or
redistribute it. The resolver degrades gracefully when it is absent, and the
ingestion run records that it was missing.

Resolution is layered by evidence strength: direct `gsis_id` (1.0), crosswalk
(0.97), name + team + position (0.85), name + position alone (0.60 — below the
auto-accept threshold, so it becomes a conflict record rather than a match).

## Alternatives considered

**Vendor the CSV.** Simplest, and a licence violation. GPL-3.0 data committed
into an MIT repository misrepresents the terms downstream users receive.

**Name matching only.** Rejected on measurement: it produces ambiguous matches
for common names, and two different Mike Williamses at wide receiver is not a
hypothetical.

**Build our own crosswalk.** Weeks of work to reproduce something maintained and
free, and it would need the same ongoing maintenance.

**Relicense this project as GPL.** Rejected: a licence should be chosen for the
project, not inherited from one data file.

## Consequences

**Good.** 100% of top-200 players resolve with zero conflicts; 97.1% across all
1,038 rostered skill players. Licence terms are respected. The system still runs
without it, at ~21% linkage, and says so.

**Bad.** A network dependency at ingestion time. An upstream change in format or
availability degrades resolution — which is why the fallback path exists and is
exercised by a test. The upstream file also contains genuine errors (three ids
claimed by two players), handled by deterministic collision resolution.
