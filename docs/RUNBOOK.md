# Runbook

Operating the system, including the twenty minutes when it actually matters.

---

## Draft night

### Before you start

```bash
make quality                                    # everything green
./.venv/bin/python -m fhe.cli ingest players    # player universe current
curl -s localhost:8000/api/v1/health | jq .degradations
```

Check the degradation list. On draft night you want an empty list, or at minimum
you want to *know* you are on SQLite and a single process.

### Symptom: the feed shows STALE

The browser has had no event for over 40 seconds.

1. Is the API alive? `curl -s localhost:8000/api/v1/health`
2. Is the poller failing? Check for `draft_poll_failed` in the logs; the
   `consecutive_failures` field tells you how long it has been going.
3. Is it a provider outage or us? `curl -s https://api.sleeper.app/v1/state/nfl`

**The board is still correct.** A stale feed means updates stopped, not that
state was lost — the poller never discards picks on failure. Reload the page to
force a canonical re-read.

If Sleeper is down: the war room keeps working on the last known state, and every
local intelligence feature (rankings, health, comparison) is unaffected.

### Symptom: a pick is missing

1. Compare against the provider directly:
   `curl -s https://api.sleeper.app/v1/draft/{id}/picks | jq 'length'`
2. Check for `draft_pick_conflict` in the logs. A conflict means the provider
   disagreed with history we recorded, and we kept ours — deliberately.
3. Check for an identity failure. An unrecognised player still consumes their
   pick and still leaves the board; they render with an unresolved name rather
   than vanishing.

### Symptom: recommendations look wrong

1. Read the component breakdown. Every score explains itself, so an implausible
   number points at which term is responsible.
2. Reproduce headlessly: `./.venv/bin/python -m fhe.cli simulate --seed 42`
3. Check replacement levels in the board payload under
   `league.replacement_ranks`. A 12-team single-QB league should read QB12,
   RB29, WR29, TE14. If it does not, league parsing is wrong, not the engine.

### Symptom: the API will not start

```bash
./.venv/bin/python -c "from fhe.api.app import create_app; create_app()"
```

Most startup failures are configuration. `Settings` validates on construction,
so the error usually names the offending variable.

---

## Ingestion

### Routine

```bash
make ingest    # players, then injuries for 2023-2025
```

Player sync is safe to re-run; it is idempotent and honours the provider's
once-per-day guidance through a 20-hour disk cache. Use `--force` only when you
know the payload changed.

### Reading a run

```bash
curl -s localhost:8000/api/v1/diagnostics/pipeline | jq
```

| Field | What it means |
| --- | --- |
| `status: failed` | Read zero rows. A run that read nothing is never a success. |
| `status: partial` | Some rows rejected. Check `rejection_reasons`. |
| `rows_rejected` | Malformed or implausible. Sampled in the run details. |
| `rows_unresolved_identity` | Fantasy-relevant players we could not link. A rising number means the crosswalk is drifting. |

### Symptom: ingestion aborted with "plausibility floor"

Working as designed. The provider returned implausibly little data, and writing
it would have marked most of the league inactive. Verify the provider directly
before overriding anything.

### Symptom: identity resolution collapsed

`rows_unresolved_identity` jumping from single digits to hundreds usually means
the crosswalk failed to download. Confirm:

```bash
ls -la data/cache/db_playerids.csv
curl -sI https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv | head -1
```

The system degrades rather than failing, so this shows up as reduced coverage,
not an error.

---

## Database

```bash
make migrate                                # apply migrations
make migration m="add contract table"       # generate one
./.venv/bin/python -m pytest tests/integration/test_migrations.py -q   # drift check
```

If the drift test fails, a model changed without a revision. Generate one rather
than editing the existing revision, unless it has never been applied anywhere.

### Resetting local state

```bash
rm -f data/fhe.db          # SQLite fallback
docker compose down -v     # Postgres volume
```

---

## Deployment

Nothing is deployed yet. Before the first one, walk
[`docs/SECURITY.md`](SECURITY.md)'s deployment checklist, then:

1. `FHE_DATABASE_URL` to managed PostgreSQL, `FHE_REDIS_URL` to managed Redis.
   Redis is **required** past one API process.
2. Run migrations as a separate job before the API starts, exactly as
   `docker-compose.yml` does.
3. Readiness at `/api/v1/health/ready`, liveness at `/api/v1/health`. Do not
   point liveness at the readiness endpoint — a database blip would restart
   healthy containers.
4. Give the API container a persistent volume for `data/cache`, or every restart
   refetches 15 MB of player data.
5. Rebuild the web image per environment: `NEXT_PUBLIC_API_BASE_URL` is compiled
   into the client bundle at build time.

---

## Monitoring

`/api/v1/metrics` exposes Prometheus text. The signals worth alerting on:

| Metric | Alert when |
| --- | --- |
| `fhe_draft_polls_total{outcome="failure"}` | Rising during an active draft |
| `fhe_provider_requests_total{outcome="error"}` | Sustained non-zero |
| `fhe_recommendation_seconds` | p99 above ~100ms |
| `fhe_ingestion_rows_total{disposition="rejected"}` | Spikes against a stable read count |
| `fhe_active_stream_clients` | Drops to zero with drafts still active |
