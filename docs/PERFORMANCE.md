# Performance

Measured, not estimated. Every number here came from a run on the machine
described below; nothing is extrapolated, and nothing is a vendor claim.

Reproduce any of it with:

```bash
make dev-api                       # terminal one
./.venv/bin/python -m fhe.cli loadtest --concurrency 20 --duration 60
```

---

## 1. What is measured, and why only reads

The harness drives the read paths a war room actually produces, weighted the
way a real client produces them: the board is re-read after every pick, the
drawer opens far less often, and the state endpoint is polled alongside. The
mix is defined in `src/fhe/loadtest/runner.py`.

Writes are deliberately excluded. A draft's write rate is bounded by how fast
twelve humans can pick — roughly one write every thirty seconds — which is not
a load-testing problem. The interesting question is what happens to *readers*
when the engine is recomputing, and that is what this measures.

## 2. Why a custom harness rather than k6 or Locust

Both are good tools and both are the wrong trade here. k6 is a separate Go
binary a contributor must install before `make` works. Locust pulls Flask,
gevent, and a web UI into a project whose only HTTP dependency is already
`httpx`. What was needed is a few hundred concurrent reads with honest
percentiles, which is a page of `asyncio` and no new dependency.

Percentiles are computed from every observation rather than a running average,
because a mean hides exactly the stall a person notices.

## 3. Environment

| | |
| --- | --- |
| Machine | Apple M1, 8 cores, 16 GB |
| OS | macOS (Darwin 23.6.0) |
| Python | 3.14.3 |
| Server | `uvicorn`, **one worker**, no reload |
| Database | SQLite (the zero-infrastructure fallback — Docker was unavailable) |
| Event bus | in-process |
| Draft | 12-team PPR demo simulation, synthetic pool |
| Client | same machine, loopback |

Loopback on the same machine as the server means client and server compete for
the same cores. Treat the absolute latencies as a floor for a real deployment,
and the *shape* of the curve as the finding.

## 4. Results

### Concurrency sweep — 10s per level

Latency in milliseconds.

| Concurrency | Throughput | board p50 | board p95 | board p99 | health p50 | Errors |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 90 req/s | 17.3 | 20.1 | 30.8 | 1.6 | 0 |
| 5 | 101 req/s | 64.3 | 94.3 | 179.7 | 15.5 | 0 |
| 20 | 105 req/s | 201.5 | 239.0 | 266.9 | 112.7 | 0 |
| 50 | 95 req/s | 479.4 | 1079.4 | 1827.0 | 291.1 | 0 |

### Soak — 60s at concurrency 20

```
concurrency 20   duration 60.2s   throughput 105 req/s   errors 0

scenario                        n     mean      p50      p95      p99      max
board (depth 120)            2549   202.3m   208.1m   252.6m   290.4m   319.6m
board (depth 40)             1267   200.1m   204.1m   252.1m   303.7m   353.0m
draft state                  1264   182.4m   180.2m   245.2m   271.2m   320.0m
health                        422   110.5m   105.6m   183.3m   208.9m   256.0m
player detail                 844   184.2m   183.6m   244.1m   280.6m   318.9m
```

Resident memory before: **106 MB**. After 6,346 requests: **103 MB**. No growth,
no latency drift across the minute, no errors.

## 5. What the numbers say

**Throughput is flat at ~90–105 req/s from concurrency 1 to 50.** That is the
signature of a single saturated worker: adding clients adds queueing, not work
done.

**The bottleneck is board evaluation, and it blocks everything else.** The
tell is the `health` endpoint, which touches nothing and returns a constant.
It costs 1.6 ms at concurrency 1 and 291 ms at concurrency 50. Nothing about
that endpoint got slower — it spent that time waiting. `session.evaluate()`
runs the full recommendation engine synchronously inside the request handler,
so while one board is being computed the event loop cannot serve anything,
including a health check.

**A single worker is nonetheless comfortably sufficient for the intended use.**
One connected client re-reads the board once per pick. A 12-team, 15-round
draft is 180 picks over a couple of hours. Even twenty simultaneous viewers of
the same draft generate a few reads per second against a floor of ninety.

**Nothing leaks.** Flat memory and flat latency across a 60-second soak at four
times the realistic load.

## 6. Limits this establishes

- **~90 board evaluations/second per worker** on this hardware, at depth 120.
- **Head-of-line blocking is real** above ~5 concurrent readers. The p99 at
  concurrency 50 is 1.8 s, which would be visible and unpleasant.
- **The knee is around concurrency 5–20.** Below it, latency is dominated by
  the work itself; above it, by queueing.

## 7. If it ever needs to go faster

In the order worth doing, and none of it is needed today:

1. **Cache the evaluated board per session** and invalidate on a pick. Reads
   between picks are identical, and today every one of them recomputes. This is
   the single biggest win and changes no architecture.
2. **Move evaluation off the event loop** with `run_in_executor`, so a slow
   board stops blocking health checks and event streams.
3. **More workers**, which requires sticky routing or shared session state —
   see the multi-worker discussion in [`DEPLOYMENT.md`](DEPLOYMENT.md) and
   [`adr/0003-zero-infrastructure-fallbacks.md`](adr/0003-zero-infrastructure-fallbacks.md).

Measured first, optimised never: the profile above says the product is fast
enough for what it does, and premature work here would trade clarity in the
engine for throughput nobody needs.

## 8. Not measured

Stated rather than quietly omitted:

- **PostgreSQL.** The Docker daemon was unavailable on this machine, so every
  number above is against SQLite. Board reads are served from an in-memory
  session and touch no database, so the board figures should be
  database-independent; the connect and ingestion paths are not measured at all.
- **SSE fan-out.** The harness makes no long-lived event-stream connections, so
  the cost of many simultaneous subscribers is unknown.
- **Ingestion throughput**, which is a batch path with no latency requirement.
- **Cold start**, which matters for a scale-to-zero deployment and was not
  timed.

---

## Live draft latency: how long until a pick shows on the board

Measured 2026-09-04 against a real Sleeper draft and the real 600-player pool.

| Step | Time |
| --- | --- |
| Sleeper `GET /draft/{id}/picks` | **30 ms** (p50) |
| Board re-evaluation after a pick | **27 ms** (p50, 600 players) |
| Browser refetch of the board | one round trip |
| **Waiting for the next poll** | **the whole rest of it** |

Everything except the wait is noise, so the poll schedule is the only thing
worth tuning.

### The schedule, and why it is shaped this way

| Situation | Worst-case delay |
| --- | --- |
| Within 3 picks of your turn | **1.0 s** |
| Mid-round, draft moving | 3.0 s |
| Draft genuinely paused (no pick for 5 min) | 9.0 s |

Two problems were fixed to get there.

**Idleness was defined as ninety seconds without a pick**, which an ordinary
manager on an ordinary two-minute clock trips on every single pick. The poller
slowed to nine seconds for the last third of each one — precisely the window the
next pick lands in — so the slower the draft, the later you heard about it. The
threshold is now five minutes, which means paused rather than thinking.

**The poller did not know whose turn it was**, though the binding has always
carried the seat. Within three picks of your turn it now polls three times
faster and never goes idle, because that is the only window where your own clock
is running and where what lands decides what is left.

### It costs less, not more

Draft metadata is fetched every fifth poll instead of every poll. It carries the
status, which changes once in a draft's life, while the picks call beside it
carries everything that changes constantly.

| | Before | After |
| --- | --- | --- |
| One draft, normal | 40 req/min | **24 req/min** |
| One draft, approaching your turn | 13 req/min | 72 req/min |

The ceiling is real now. Every `SleeperProvider` built its own `RateLimiter`, and
one is built per live draft and per request-scoped lookup — so twelve pollers
each politely staying under a 600/min ceiling added up to twelve times the
ceiling, against a limit Sleeper enforces per IP. Providers now share one
limiter per rate, so the process cannot exceed 600/min however many drafts it
follows; excess requests queue rather than failing, which degrades every
follower smoothly instead of blacking one out.
