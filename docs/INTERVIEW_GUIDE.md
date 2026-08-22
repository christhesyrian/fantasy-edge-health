# Interview guide

The decisions in this project worth defending, the trade-offs behind them, and
the questions a senior reviewer is likely to ask.

Written for the author's own use. It assumes the reader has the code open.

---

## The thirty-second summary

> A fantasy football draft assistant that answers "who should I take right now"
> by combining projections with availability risk, positional scarcity, roster
> need, ADP value, and the probability a player survives until your next pick.
> The recommendation engine is pure — no I/O — so the live Sleeper draft, the
> mock simulator, and the tests all drive identical code. Every score is
> decomposable: the components always sum to the headline number, and a test
> asserts it.

Then let them pick a thread.

---

## The three things actually worth talking about

### 1. Identity resolution, because it was measured rather than assumed

The obvious approach is joining Sleeper to nflverse on `gsis_id`. Measuring
first showed Sleeper publishes it for **21% of top-200 fantasy players**, and
nflverse publishes no Sleeper id at all. A direct join would have stranded four
fifths of the pool — and the failure would have been *quiet*, showing up as
missing injury history rather than an error.

Adding a runtime crosswalk took it to 100% of the top 200 with zero conflicts.
Anything below the auto-accept confidence threshold becomes a row in
`player_identity_conflicts` rather than a silent guess.

The reusable point: **measure the join before designing around it.**

### 2. The bugs found by running against real data

Each of these passed unit tests and would have embarrassed a demo:

- **A kicker ranked third overall.** Kickers had no replacement baseline, so
  their value over replacement equalled their entire projection.
- **A round-14 defense scored like the 1.01.** VORP normalised against the
  *available* pool, so the best remaining player always scored full value.
- **Re-importing projections duplicated every row.** A unique constraint
  containing a nullable `week` cannot deduplicate, because SQL treats `NULL` as
  never equal to `NULL`.
- **The live event stream connected, reported healthy, and delivered nothing.**
  The server emits named SSE events; `EventSource.onmessage` fires only for
  unnamed ones.
- **A subscriber registered lazily.** `subscribe()` was an async generator, so
  its body ran only on first `__anext__` — losing every event published between
  the handler starting and its first loop tick, which during a draft is exactly
  when picks arrive.

All five are pinned by regression tests named after the symptom.

The reusable point: **unit tests prove the code does what you wrote; running it
proves you wrote the right thing.**

### 3. Designing for the failure that actually matters

The product has exactly one moment where reliability counts: twenty minutes of a
live draft. Everything about the provider layer follows from that.

- Self-limited to 600 req/min against a documented 1000 ceiling, because an IP
  block is unrecoverable *within the session*.
- A provider outage never wipes the board. An empty response is never a reset.
- 404 semantics differ per endpoint on purpose: a missing league returns `None`
  because that is normal onboarding, while missing draft picks *raise*, because
  "this draft vanished" and "nobody has picked yet" must stay distinguishable.
- The browser never patches a local board from events; it re-reads canonical
  state. A board that has drifted from the engine is worse than one that lags,
  because the user cannot tell which they are looking at.

---

## Questions to expect

**"Why not use an ML model for the recommendation score?"**
There is no ground truth for "correct pick", so it would be fitted to a proxy
and could not be defended at the table. The place for a learned model is the
availability estimate, which produces one calibrated probability entering the
sum as a single term. That model is scaffolded and deliberately not enabled —
promoting it requires a leakage audit, time-based splits, calibration, and a
demonstrated out-of-sample win over the heuristic. A model that trains is not a
model that works.

**"Your health model isn't validated. Isn't that a problem?"**
Yes, and it is labelled heuristic for exactly that reason. Its weights are
reasoned, not fitted. What makes it *usable* rather than merely unvalidated is
that every contribution is itemised and explained, so a user can disagree with a
specific term. The failure mode I designed hardest against is a player with no
data reading as "safe" — missing data lowers confidence, and the war room's
"safest pick" slot requires low risk *and* sufficient confidence.

**"Why SSE instead of WebSockets?"**
Communication is almost entirely server-to-client. SSE reconnects natively with
no client library and survives proxies that mangle upgrades. The capability
WebSockets add is unused. See ADR 0005, including the two silent bugs.

**"How would this scale?"**
The engine is pure and CPU-bound at ~9 ms per board, so it scales with
processes. The real limits are elsewhere: draft sessions are in memory (Redis or
sticky routing required past one process), and Sleeper's rate limit is per IP,
so it is shared across every worker behind one egress address. Named in
`infra/README.md` rather than discovered later.

**"What would you do differently?"**
Ingest weekly stats and snap counts earlier — workload is currently the
weakest-evidenced input to the health model. And I would have written the
architecture purity test on day one rather than after the domain existed; it
would have caught two boundary violations while they were cheap.

**"What's the weakest part?"**
The survival probability's normality assumption. Real draft-position
distributions are right-skewed, and the faller re-anchoring is a correction
rather than a fix. It should be replaced with an empirical distribution from
simulated drafts — which the deterministic simulator makes straightforward, and
which I have not done yet.

**"Why is so much of this documentation about limitations?"**
Because the product's core claim is that its numbers are honest. Documentation
that overstates what the system knows would contradict the thing it is
documenting.

---

## Numbers worth remembering

| | |
| --- | --- |
| Tests | 411 Python, 33 frontend |
| Injury taxonomy coverage | 99.97% of 62,915 real observations |
| Identity resolution | 100% of top 200, 97.1% of all rostered |
| Board recompute | 9.3 ms median in-engine; 10–17 ms end to end |
| Sleeper rate limit | 600/min self-imposed vs 1000 documented |
| Tables | 26, provenance on every observation |
| Sleeper `gsis_id` coverage | 21% of top-200 — the measurement that shaped the design |

---

## What to demo, in order

1. **Start with no setup.** `make dev-api` and `make dev-web`, nothing else. No
   database, no Docker, no credentials.
2. **Press `a`.** The draft runs to your pick; the board reacts.
3. **Point at the score breakdown.** Every number explains itself.
4. **Draft the top player.** Roster fills, ticker marks it as yours, a tier-cliff
   alert fires, survival probabilities collapse now that your next pick is
   fourteen away.
5. **Open a player drawer.** Injury timeline with the provider's *original*
   wording preserved beside the normalised region, and the model's limitations
   printed on screen.
6. **Then show `tests/unit/test_draft_poller.py`** — the failure modes, not the
   happy path. That file is the most senior thing in the repository.
