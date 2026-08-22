# FANTASY HEALTH EDGE — MASTER BUILD DIRECTIVE

You are the principal engineer responsible for designing and implementing a production-quality fantasy football intelligence platform named **Fantasy Health Edge**.

This is not a toy project, tutorial, proof of concept, or static dashboard.

Build this as if it were:

1. A real product used during a live fantasy football draft.
2. A production software engineering system expected to survive real users and real-time events.
3. A senior-level software/data engineering portfolio project.
4. A system that may eventually become a commercial product.
5. A codebase that will be scrutinized by senior software engineers, data engineers, ML engineers, security engineers, and hiring managers.

You are authorized to create, modify, test, refactor, document, and organize the repository as necessary.

Do not merely give me instructions.

**Actually build the application.**

Continue implementing independent work until there is genuinely something that requires me to provide credentials, make an account, select a league, approve a paid service, or perform another action that only I can perform.

When human action is required:

- Explain exactly what I need to do.
- Explain why.
- Provide exact commands or UI steps.
- Add the requirement to `USER_ACTION_REQUIRED.md`.
- Continue implementing everything else that does not depend on that action.
- Never commit secrets.
- Never fabricate credentials.
- Never substitute fake production integration for a missing credential without clearly labeling it.

---

# 1. YOUR ROLES

Act simultaneously as an expert:

- Principal Software Engineer
- Software Architect
- Senior Backend Engineer
- Senior Frontend Engineer
- Senior Data Engineer
- Data Architect
- Machine Learning Engineer
- MLOps Engineer
- DevOps Engineer
- Site Reliability Engineer
- Database Engineer
- API Integration Engineer
- Security Engineer
- QA/Test Automation Engineer
- Product Engineer
- UX Engineer
- Accessibility Engineer
- Fantasy Football Analytics Engineer
- NFL Data Analyst
- Technical Writer
- Code Reviewer

Think like someone who has built large production systems.

Optimize for:

- correctness
- maintainability
- observability
- explainability
- testability
- performance
- developer experience
- clean architecture
- realistic data engineering
- attractive UX
- safe external API usage
- excellent documentation

Do not optimize simply for writing the most code.

---

# 2. CLAUDE CODE ORCHESTRATION

Use Claude Code's current capabilities where appropriate.

Before creating any Claude-specific configuration, inspect the installed Claude Code version and verify the syntax against current official Claude Code documentation.

Do not guess configuration schemas.

Create a concise project `CLAUDE.md` containing persistent engineering conventions and architectural rules.

Keep it focused. Put large procedures into skills or specialized agent definitions instead.

Create useful specialized subagents in:

`.claude/agents/`

Recommended agents:

- `architect`
- `data-engineer`
- `backend-engineer`
- `ml-engineer`
- `frontend-engineer`
- `qa-security-reviewer`
- `devops-engineer`

Delegate substantial isolated tasks to appropriate subagents.

Use parallelism when tasks are independent.

If the current Claude Code installation supports Agent Teams and they are appropriate, use them for large independent workstreams. Otherwise use normal subagents.

Create reusable Claude skills where useful, including concepts such as:

- `/quality-gate`
- `/data-source-audit`
- `/draft-simulation`
- `/security-review`
- `/release-check`

Use deterministic command hooks only where their current schema is verified and where they provide real value, such as:

- formatting after modifications
- preventing accidental secret commits
- checking lint/test results
- preventing obviously destructive commands

Do not create brittle or experimental automation merely because it exists.

Maintain a repository-level task/progress document while working.

---

# 3. PRODUCT VISION

Build **Fantasy Health Edge**, a data-driven fantasy football decision platform.

The central question is:

> "Who should I draft right now after accounting for expected fantasy production, injury/availability risk, roster construction, positional scarcity, ADP, and the probability that a player survives until my next pick?"

The application should ultimately function as a **live fantasy draft war room**.

Primary capabilities:

### Live Draft Assistant

Connect to a Sleeper league/draft and automatically:

- detect new picks
- remove drafted players
- identify which team made each selection
- update each fantasy roster
- determine when my pick is approaching
- recalculate recommendations
- recalculate positional scarcity
- recalculate roster needs
- show best available players
- show injury alerts
- show ADP value
- predict whether a player may survive until my next pick
- recommend "Draft Now", "Likely Available Later", "Value", "Reach", or "Avoid/Discount"

The UI should update during the draft without requiring manual refresh.

### Injury Intelligence

For each player show:

- current status
- injury designation
- injury start date if known
- practice participation
- historical injury events
- primary body region
- recurrent injury indicators
- historical games impacted
- recent workload
- player age
- years experience
- position
- risk score
- availability estimate
- confidence
- explanations for the risk score

### Injury-Adjusted Fantasy Rankings

Calculate rankings that combine:

- fantasy projections
- expected availability
- positional replacement value
- positional scarcity
- current roster requirements
- ADP
- draft cost
- workload
- upside
- injury/availability risk
- probability of surviving until user's next selection

### Player Comparison

Allow 2–4 players to be compared across:

- projection
- ADP
- model rank
- value over replacement
- injury risk
- health trend
- workload
- age
- current status
- recent fantasy performance
- position
- team
- availability
- model explanations

### Player Detail

Every fantasy-relevant player should have a detail view/drawer containing:

- profile
- fantasy projection
- current health status
- health risk score
- historical injury timeline
- practice history when available
- workload history
- weekly fantasy history
- depth-chart information
- ranking
- ADP
- model ranking
- value delta
- explainability panel
- data freshness
- source provenance

### Draft Simulation

Build a mock-draft simulator that allows the recommendation algorithm to be tested before a real draft.

Use configurable:

- league size
- scoring format
- roster format
- draft position
- snake/linear draft
- ADP randomness
- positional tendencies

This simulator must exercise the same recommendation engine as the live system.

---

# 4. IMPORTANT DATA-SOURCE RULE

External data correctness is critical.

**Never invent an API endpoint, field, credential requirement, refresh cadence, license, data availability date, or provider feature.**

Before implementing an external provider:

1. Locate current official documentation.
2. Verify the endpoint/schema.
3. Record the source in `docs/DATA_SOURCES.md`.
4. Record known limitations.
5. Create fixture-based contract tests.
6. Implement retries and timeouts.
7. Gracefully degrade if unavailable.

Do not scrape a site simply because an API does not exist.

Do not circumvent authentication or provider restrictions.

Respect provider terms and rate limits.

---

# 5. SLEEPER INTEGRATION

Sleeper should be the primary live fantasy integration for version 1.

Verify all endpoints against current official Sleeper documentation before implementation.

Build a typed `SleeperProvider`.

Support at minimum:

- user lookup
- leagues
- league information
- league users
- rosters
- drafts
- individual draft
- draft picks
- NFL players
- NFL state
- trending adds
- trending drops

Use Sleeper's player IDs as a source identifier, not as our internal universal primary key.

The full Sleeper NFL player payload is large and should be cached instead of requested repeatedly.

Implement a daily synchronization policy for the complete player universe unless official documentation indicates a different safe approach.

Persist relevant fields including, where currently available:

- Sleeper player ID
- name
- team
- position
- fantasy positions
- status
- injury status
- injury start date
- practice participation
- depth-chart fields
- age
- years experience
- provider IDs

### Live Sleeper Draft Polling

Implement a resilient live polling service.

Do not hammer Sleeper.

Use an adaptive/default interval around a few seconds, remaining comfortably inside documented rate limits.

Workflow:

Sleeper draft endpoint
→ retrieve current pick list
→ compare against last observed state
→ identify new pick(s)
→ validate idempotency
→ persist picks
→ update available-player pool
→ update rosters
→ publish internal draft event
→ recompute recommendation state
→ send update to UI

Handle:

- multiple picks appearing between polls
- duplicate poll results
- reordered responses
- temporary API failures
- timeouts
- 429s
- 5xx errors
- draft completion
- traded picks if applicable
- keepers if applicable
- missing player metadata

Use exponential backoff with jitter when appropriate.

Never duplicate a pick in our database.

---

# 6. NFLVERSE

Use nflverse for historical/open NFL data where appropriate.

Verify all current datasets and stable download mechanisms before implementation.

Important:

Historical nflverse injury data currently ends after the 2024 season because the upstream injury source stopped providing data.

**Do not treat nflverse as a source of current 2025/2026 injury reports unless official documentation changes and you verify that change.**

Useful nflverse datasets may include:

- historical injuries
- players
- player IDs
- weekly player stats
- rosters
- depth charts
- snap counts
- play-by-play-derived data

Build a provider abstraction.

Do not require R merely to run the production application if stable official parquet/CSV assets can be consumed from Python.

Prefer efficient columnar formats such as Parquet when available.

Use Polars and/or PyArrow where it improves ingestion performance.

---

# 7. PLAYER IDENTITY RESOLUTION

Identity resolution is a first-class engineering problem.

Create an internal immutable:

`player_uuid`

Maintain source crosswalks containing fields such as:

- gsis_id
- sleeper_id
- espn_id
- yahoo_id
- fantasypros_id
- pfr_id
- sportradar_id
- fantasy_data_id

Use trusted crosswalk data where available.

Prefer deterministic ID mappings.

Never match only by player name unless no identifier exists.

When fuzzy matching is unavoidable use supporting dimensions such as:

- normalized name
- team
- position
- birth date
- jersey
- season

Assign confidence.

Send ambiguous identity records to an explicit reconciliation table rather than silently selecting a player.

Create:

`player_identity_conflicts`

with enough metadata to investigate unresolved matches.

---

# 8. ADP AND PROJECTIONS

Create provider interfaces:

`AdpProvider`

`ProjectionProvider`

Do not hard-code the application to a single commercial fantasy provider.

Implement a manual CSV import path from day one so the system remains useful without paid APIs.

Define validated CSV schemas.

Potential future providers can be plugged in only after verifying their current API availability, terms, and licenses.

Do not scrape FantasyPros, ESPN, Yahoo, Rotowire, or other providers in violation of their terms.

For every metric show its provider and timestamp.

---

# 9. ARCHITECTURE

Build this as a clean monorepo.

A good starting architecture is:

```text
fantasy-health-edge/
├── apps/
│   └── web/
├── services/
│   ├── api/
│   └── worker/
├── packages/
│   ├── contracts/
│   └── config/
├── ml/
├── data/
│   ├── fixtures/
│   └── schemas/
├── infra/
├── docs/
│   ├── adr/
│   └── diagrams/
├── scripts/
├── tests/
├── .claude/
│   ├── agents/
│   ├── skills/
│   └── rules/
├── .github/
│   └── workflows/
├── CLAUDE.md
├── README.md
├── USER_ACTION_REQUIRED.md
└── docker-compose.yml
```

Adjust this architecture if there is a strong engineering reason.

Document major deviations with an ADR.

---

# 10. RECOMMENDED TECH STACK

Unless investigation reveals a compelling incompatibility, use:

## Frontend

- Next.js
- React
- TypeScript strict mode
- Tailwind CSS
- shadcn/ui where appropriate
- TanStack Query
- TanStack Table
- Zod
- Recharts or another lightweight visualization library
- Vitest
- React Testing Library
- Playwright

Use current stable compatible versions.

Do not blindly use a version from this prompt if a more current stable release exists.

## Backend

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2
- Alembic
- httpx
- PostgreSQL
- Redis

Use async I/O appropriately.

## Data

- PostgreSQL curated operational/analytical entities
- S3-compatible object storage for immutable/raw ingestion artifacts
- MinIO locally
- Polars/PyArrow for data processing
- Pandera or equivalent for dataframe validation
- dbt only if it adds meaningful transformation value rather than ceremony

## ML

- pandas/Polars as appropriate
- NumPy
- scikit-learn
- XGBoost or LightGBM only if justified by validation
- calibration tools
- SHAP or robust alternative for explainability where appropriate

## Data orchestration

Use Prefect or another lightweight appropriate orchestrator.

Do not introduce Airflow solely for résumé buzzwords.

If Prefect is sufficient, prefer it.

## Infrastructure

- Docker
- Docker Compose
- GitHub Actions
- Vercel-friendly frontend
- container-friendly API/worker
- AWS-compatible production architecture

The system must run locally without requiring AWS.

---

# 11. DATABASE DESIGN

Create a normalized, migration-managed schema.

Likely entities include:

### Core

`players`

`player_external_ids`

`teams`

`seasons`

### Health

`injury_events`

`current_player_health`

`practice_reports`

`health_score_snapshots`

`availability_predictions`

### Football

`player_weekly_stats`

`snap_counts`

`depth_chart_snapshots`

`roster_status_snapshots`

### Fantasy

`fantasy_projections`

`adp_snapshots`

`fantasy_rankings`

### Draft

`fantasy_leagues`

`league_settings`

`drafts`

`draft_slots`

`draft_picks`

`fantasy_rosters`

`roster_players`

`draft_recommendation_snapshots`

### Data Engineering

`data_ingestion_runs`

`data_quality_results`

`provider_sync_state`

`player_identity_conflicts`

Every time-sensitive table should include appropriate fields such as:

- source
- source_updated_at
- ingested_at
- valid_from
- observed_at

where semantically appropriate.

Use indexes deliberately.

Use uniqueness constraints for idempotency.

Avoid storing arbitrary JSON when structured columns should exist.

Use JSONB where provider payload preservation is genuinely valuable.

---

# 12. RAW → STAGING → CURATED DATA FLOW

Implement clear lineage.

```text
External Provider
       ↓
Raw immutable payload
       ↓
Schema validation
       ↓
Normalized staging representation
       ↓
Identity resolution
       ↓
Curated PostgreSQL entities
       ↓
Feature engineering
       ↓
Health/availability model
       ↓
Draft intelligence engine
       ↓
FastAPI
       ↓
Next.js War Room
```

Never silently discard malformed records.

Track:

- ingestion run
- provider
- requested endpoint/dataset
- start/end
- row counts
- errors
- rejected rows
- checksum/version when useful
- freshness

---

# 13. DATA QUALITY

Implement automated quality checks.

Examples:

- duplicate player IDs
- null primary keys
- invalid NFL positions
- impossible ages
- impossible weeks
- unknown teams
- duplicate draft pick numbers
- unresolved player IDs
- negative snap counts
- invalid ADP
- projections outside rational bounds
- stale provider data
- incoming schema drift

Expose recent pipeline health in an internal health endpoint and/or developer diagnostics page.

Do not allow a corrupt provider response to overwrite known-good current state.

---

# 14. HEALTH RISK ENGINE

This is not a medical diagnosis system.

Do not make claims such as predicting a specific body injury will happen.

We are estimating fantasy-relevant **availability risk** from historical football data.

Create two modes:

### A. Transparent Heuristic Model

Must work before ML is trained.

Features may include:

- current injury designation
- current roster status
- practice participation
- injury recency
- injury event count
- recurrent same-region injuries
- games impacted
- age
- years experience
- position
- recent workload
- snaps
- carries
- targets
- QB hits/sacks where relevant and defensible
- previous-season usage

Return:

`health_risk_score: 0–100`

and components explaining the score.

### B. Validated ML Availability Model

Do not activate an ML prediction in production simply because a model trains.

Build a defensible target such as the probability of injury-related unavailability over an explicitly defined horizon, based only on fields that would have been known at prediction time.

Prevent temporal leakage.

Use time-based train/validation/test splits.

Establish a simple baseline first.

Evaluate:

- ROC-AUC where appropriate
- PR-AUC
- Brier score
- calibration
- precision/recall at actionable thresholds
- reliability plots

Calibrate predicted probabilities.

Compare candidate models against baseline.

Only promote a more complex model if it meaningfully improves out-of-sample quality.

Persist:

- model version
- training date
- dataset range
- features
- metrics
- calibration information

Create:

`docs/MODEL_CARD.md`

Explain limitations prominently.

---

# 15. INJURY NORMALIZATION

Raw injury text varies.

Create a controlled injury taxonomy.

Potential high-level regions:

- head/concussion
- neck
- shoulder
- arm/elbow
- hand/wrist/finger
- torso/ribs
- back
- hip/groin
- hamstring
- quadriceps
- knee
- calf
- ankle
- foot/toe
- illness
- rest
- other/unknown

Preserve the raw provider value.

Never discard source terminology.

Create a normalization mapping with tests.

Do not infer medical severity unsupported by the provider.

---

# 16. PRACTICE TRAJECTORY

Model practice status independently from game designation.

Normalize states such as:

- DNP
- LIMITED
- FULL
- UNKNOWN

Calculate recent trajectory where reports exist.

Examples:

`DNP → LIMITED → FULL`

improving.

`FULL → LIMITED → DNP`

worsening.

Treat this as one signal rather than absolute certainty.

---

# 17. DRAFT VALUE ENGINE

Build a deterministic, unit-tested recommendation engine separate from the UI.

It must be possible to execute this engine from Python tests with no frontend.

Primary components:

### Projected Value

Projected fantasy points or ranking.

### Value Over Replacement

Estimate replacement-level output by league configuration.

### Positional Scarcity

Determine how quickly useful talent is disappearing by position.

### Tier Dropoff

Identify significant projected-value drops between nearby players at a position.

### Roster Need

Account for:

- required starters
- FLEX
- SUPERFLEX when applicable
- bench composition
- roster construction

### Health Adjustment

Apply validated availability/risk penalties.

### ADP Value

Compare:

`market ADP`

against:

`model rank`

Create:

`value_delta`

### Next-Pick Survival Probability

This should be a flagship feature.

Estimate:

> Probability player X remains available by my next selection.

Use:

- ADP
- ADP dispersion if available
- number of selections until next pick
- positions already drafted
- team roster needs
- draft format

A simple probabilistic model is acceptable initially.

Later improve it with empirical/simulated draft behavior.

Expose:

- `take_now_probability`
- `survival_to_next_pick_probability`

### Final Recommendation

Produce a structured recommendation such as:

```json
{
  "player_id": "...",
  "overall_score": 91.4,
  "model_rank": 14,
  "market_adp": 23.7,
  "adp_value": 9.7,
  "health_risk": 18,
  "next_pick_survival_probability": 0.21,
  "recommendation": "DRAFT_NOW",
  "reasons": [
    "...",
    "...",
    "..."
  ]
}
```

Do not generate explanations with arbitrary LLM prose.

Core recommendation reasons should come from deterministic structured facts.

---

# 18. LIVE DRAFT ARCHITECTURE

Use server-sent events or WebSockets for real-time browser updates.

Prefer SSE if communication is predominantly server → client and it keeps the architecture simpler.

Suggested pattern:

```text
Sleeper
   ↓
Draft Poll Worker
   ↓
Pick Deduplicator
   ↓
Postgres
   ↓
Redis Pub/Sub
   ↓
Recommendation Engine
   ↓
Draft State Snapshot
   ↓
SSE
   ↓
Browser War Room
```

The frontend must reconnect gracefully.

If the real-time connection drops:

- show connection status
- retry automatically
- avoid duplicate events
- fetch canonical state after reconnect

Show:

`LIVE`

`RECONNECTING`

`STALE`

statuses.

---

# 19. DRAFT WAR ROOM UX

This is the flagship screen.

Design desktop-first for a laptop beside the actual fantasy draft.

The screen should feel like a professional sports operations dashboard, not an admin template.

Suggested composition:

### Top bar

- Fantasy Health Edge branding
- league
- scoring format
- draft format
- current pick
- next user pick
- connection status
- data freshness
- settings

### Left/main section

**Best Available**

High-density sortable table with:

- rank
- player
- position
- team
- projection
- ADP
- model rank
- value
- health score
- survival probability
- action recommendation

Filters:

- All
- QB
- RB
- WR
- TE
- FLEX
- Favorites
- Healthy
- Value
- Avoid

### Center recommendation panel

Prominently show:

**BEST PICK RIGHT NOW**

with:

- player
- recommendation
- total score
- expected value
- health
- ADP advantage
- probability player survives next pick
- 3–5 concise reasons

Also show:

- Safest Pick
- Highest Upside
- Best ADP Value

### Right side

**My Roster**

By starting slot:

- QB
- RB
- RB
- WR
- WR
- TE
- FLEX
- etc.

Show bench below.

Highlight unmet starter positions.

### Draft ticker

Show most recent picks.

### Alerts

Examples:

- "RB tier about to drop"
- "Only 2 Tier-2 WRs remain"
- "Player X has fallen 13 picks below ADP"
- "Player Y health status changed"
- "Your pick is in 3 selections"

---

# 20. PLAYER DRAWER

Clicking a player should open a sophisticated detail drawer/modal without losing draft context.

Sections:

### Overview

- headshot if licensing/source allows
- name
- team
- position
- age
- experience
- ADP
- projection
- ranking

### Health

- current designation
- health risk gauge
- availability estimate
- confidence
- practice status

### Injury Timeline

Chronological visualization.

### Usage

Charts for:

- snaps
- carries
- targets
- routes if available
- fantasy points

### Value

- market ADP
- model rank
- value delta
- VORP
- survival-to-next-pick probability

### Explanation

Explain exactly what drove the recommendation.

---

# 21. VISUAL DESIGN

Do not create a generic shadcn dashboard.

Do not create a generic AI purple gradient interface.

Do not copy Sleeper, ESPN, Yahoo, or NFL branding.

Create an original **sports analytics war-room aesthetic**.

Design principles:

- dark mode as the primary experience
- restrained accent colors
- strong information hierarchy
- crisp typography
- excellent spacing
- high information density
- subtle depth
- subtle motion
- modern data visualization
- professional rather than gamer-cheesy
- instantly readable under draft-clock pressure

Health severity should be distinguishable by more than color alone.

Support:

- dark mode
- light mode
- responsive design
- keyboard navigation
- accessible contrast
- reduced-motion preference

Use skeleton states instead of layout shifts.

Use polished empty, error, reconnecting, loading, and stale-data states.

---

# 22. COMMAND PALETTE AND KEYBOARD SUPPORT

Add productivity interactions.

Examples:

`⌘/Ctrl + K`

opens command palette.

Possible commands:

- Search player
- Show RB
- Show WR
- Open recommendations
- Open my roster
- Compare selected players
- Toggle favorites
- Open settings

Keyboard shortcuts should not conflict with browser accessibility.

---

# 23. ONBOARDING

Create a polished onboarding flow.

Option A:

**Connect Sleeper**

User enters:

- Sleeper username

Application:

1. resolves user
2. retrieves current NFL leagues
3. allows league selection
4. retrieves drafts
5. allows active/upcoming draft selection
6. identifies user's roster/draft slot
7. loads league settings

Option B:

**Manual League**

Allow use without Sleeper.

Configure:

- number of teams
- PPR/half-PPR/standard
- custom scoring
- roster positions
- draft position
- snake/linear
- number of rounds

Option C:

**Demo Mode**

Use seeded deterministic data and a simulated live draft so anyone reviewing the GitHub project can experience the application without credentials.

Demo mode must be unmistakably labeled.

---

# 24. MOCK DRAFT SIMULATOR

Build a simulation engine.

Other teams should make probabilistic selections influenced by:

- ADP
- positional needs
- scarcity
- randomness

Allow:

- simulation speed
- pause
- next pick
- auto-run
- reset
- random seed

This should let me test the exact War Room UI before draft day.

Use deterministic seeds for automated testing.

---

# 25. NATURAL LANGUAGE ASSISTANT — LATER PHASE

After core analytics are working, add an optional AI assistant interface.

Examples:

- "Who is the safest RB available?"
- "Should I take this WR now or wait?"
- "Compare these three players."
- "Why are you fading this player?"
- "What position should I target next?"

The LLM must operate over structured application data.

It must not invent:

- injuries
- rankings
- draft picks
- projections
- statuses

Provide retrieved data and timestamps as context.

Where reasonable, make the deterministic recommendation engine authoritative and the LLM merely explanatory.

The product must remain useful without an LLM API key.

---

# 26. API DESIGN

Create a clean versioned REST API.

Potential endpoints:

```text
GET    /api/v1/health
GET    /api/v1/players
GET    /api/v1/players/{id}
GET    /api/v1/players/{id}/health
GET    /api/v1/players/{id}/history
GET    /api/v1/rankings

GET    /api/v1/sleeper/users/{username}
GET    /api/v1/sleeper/leagues/{user_id}
POST   /api/v1/leagues/connect

GET    /api/v1/drafts/{id}
GET    /api/v1/drafts/{id}/state
GET    /api/v1/drafts/{id}/recommendations
GET    /api/v1/drafts/{id}/events

POST   /api/v1/simulations
POST   /api/v1/simulations/{id}/advance
POST   /api/v1/simulations/{id}/reset

POST   /api/v1/import/adp
POST   /api/v1/import/projections
```

This list is conceptual.

Design final endpoints using REST semantics and actual domain needs.

Publish OpenAPI docs automatically.

Generate or validate frontend API types against the backend contract.

---

# 27. CACHING

Use Redis deliberately.

Candidates:

- current draft state
- recommendation snapshots
- high-read player summaries
- provider rate-limit/backoff state
- pub/sub draft events

Do not cache blindly.

Document TTL reasoning.

Cache invalidation must be explicit.

---

# 28. PERFORMANCE

The War Room must feel instant.

Targets:

- cached ranking queries: very fast
- draft recommendation recompute: ideally sub-second
- live draft pick → browser update: low seconds at worst, dominated by provider poll interval

Do not recalculate expensive historical features on every frontend request.

Precompute feature snapshots.

Profile before premature optimization.

---

# 29. RESILIENCE

External fantasy providers can fail during draft night.

Design for degradation.

If Sleeper fails temporarily:

- retain last known draft state
- show stale warning
- continue serving local player intelligence
- retry safely
- never wipe the draft

If ADP source fails:

- use last valid snapshot
- identify it as stale

If health data fails:

- retain last valid snapshot
- expose timestamp

Every important metric must know when it was last updated.

---

# 30. SECURITY

Apply production-minded security.

At minimum:

- `.env` ignored
- `.env.example`
- secrets only through environment variables
- no credentials in source
- input validation
- request size limits where relevant
- safe file upload handling for CSV
- CORS configured intentionally
- secure production defaults
- dependency scanning
- no arbitrary code execution
- no unsafe deserialization
- SQL parameterization through ORM/query builder
- sanitize untrusted display strings
- rate limiting on expensive endpoints where useful

Add:

- secret-scanning/pre-commit strategy
- Dependabot configuration if appropriate
- CI security checks

Never expose server-only secrets through Next.js public environment variables.

---

# 31. OBSERVABILITY

Add:

### Structured Logs

Include:

- timestamp
- level
- service
- request ID
- provider
- operation
- draft ID where relevant
- duration
- error category

Do not log secrets.

### Metrics

At minimum expose/track concepts such as:

- provider request count
- provider request latency
- provider failures
- poll success/failure
- draft event lag
- recommendation computation latency
- ingestion runs
- rejected records
- active SSE/WebSocket clients

### Health endpoints

Differentiate:

- liveness
- readiness

Optionally integrate OpenTelemetry/Sentry in a pluggable fashion.

Do not require paid observability to run locally.

---

# 32. TESTING

Testing is mandatory.

## Backend Unit Tests

Cover:

- identity resolution
- injury normalization
- health scoring
- recommendation scoring
- positional scarcity
- VORP
- next-pick math
- roster needs
- pick idempotency
- draft state transitions

## Provider Contract Tests

Use saved sanitized fixtures.

Do not make the normal test suite depend on external internet services.

Create optional integration tests that can hit live providers manually.

## Data Tests

Validate:

- schemas
- row uniqueness
- key relationships
- ranges
- unexpected categories

## Frontend Tests

Use:

- Vitest
- React Testing Library

Test important components and state transitions.

## E2E

Use Playwright.

Critical E2E flow:

1. open demo mode
2. start mock draft
3. picks arrive
4. available players update
5. recommendations update
6. user makes selection
7. roster updates
8. next recommendation appears
9. player drawer opens
10. compare players

## Resilience Tests

Simulate:

- 429
- timeout
- malformed response
- duplicate draft pick
- Redis outage where feasible
- API reconnect
- stale data

---

# 33. TEST DATA

Create realistic but legal test fixtures.

Do not ship copyrighted/proprietary bulk datasets in Git.

Where possible generate synthetic data.

A small provider response fixture may be stored when legally appropriate for contract testing.

Clearly distinguish:

- fixture
- synthetic
- production

---

# 34. CI/CD

Create GitHub Actions.

For pull requests run:

### Python

- formatting check
- Ruff
- type checking
- pytest
- migrations sanity check

### TypeScript

- formatting
- ESLint
- TypeScript check
- unit tests
- production build

### Integration

- Docker Compose/service tests when appropriate
- Playwright smoke test

### Security

- dependency vulnerability checks
- secret scanning where practical

Do not deploy from a failing build.

---

# 35. LOCAL DEVELOPMENT

I should eventually be able to run something close to:

```bash
docker compose up --build
```

and obtain:

- PostgreSQL
- Redis
- MinIO if used
- API
- worker
- frontend

Also support efficient native development where appropriate.

Create scripts/Makefile/task runner for common operations.

Examples:

```text
dev
test
lint
format
typecheck
seed
ingest
simulate-draft
quality
```

Make Windows development reasonable as well as macOS/Linux.

Avoid scripts that work only in Bash unless a cross-platform alternative exists.

---

# 36. DOCUMENTATION

The repo must have exceptional documentation.

Create:

### README.md

Include:

- product screenshot placeholders
- what the project does
- architecture
- technologies
- features
- data sources
- local setup
- demo mode
- testing
- deployment
- ML overview
- limitations
- roadmap

### docs/ARCHITECTURE.md

Deep technical architecture.

### docs/DATA_SOURCES.md

For every source:

- purpose
- official documentation
- refresh pattern
- fields used
- limitations
- license/terms considerations
- last verified date

### docs/DATA_MODEL.md

Explain schema.

### docs/DRAFT_ENGINE.md

Explain recommendation mathematics.

### docs/INJURY_MODEL.md

Explain features and limitations.

### docs/MODEL_CARD.md

ML evaluation and limitations.

### docs/RUNBOOK.md

Operational troubleshooting.

### docs/SECURITY.md

Threat model and security decisions.

### docs/INTERVIEW_GUIDE.md

Explain:

- architecture
- technical decisions
- tradeoffs
- scalability
- failure modes
- likely interview questions

### ADRs

Create ADRs for major decisions.

Use Mermaid diagrams where helpful.

---

# 37. CODE QUALITY

Non-negotiable:

- TypeScript strict mode
- typed Python
- modular architecture
- no giant god files
- no duplicated business logic
- no frontend-only recommendation logic
- no unexplained magic numbers
- no silent exceptions
- no bare except
- no unused dead code
- no TODOs without explanation
- no fake completed integrations
- no placeholder API responses presented as production
- no committed secrets

Favor domain-oriented naming.

Business rules belong in a testable domain/service layer.

---

# 38. DEFINITION OF DONE FOR EACH PHASE

Never declare a phase complete because files exist.

A phase is complete only after:

1. implementation exists
2. formatter succeeds
3. linter succeeds
4. type checking succeeds
5. unit tests succeed
6. relevant integration tests succeed
7. build succeeds
8. documentation reflects actual implementation
9. no known critical errors remain
10. git diff has been reviewed for accidental secrets/debugging artifacts

If something cannot pass, investigate root cause.

Do not disable tests merely to obtain green CI.

---

# 39. IMPLEMENTATION PHASES

Work in this order unless architecture investigation provides a compelling reason to adjust.

## Phase 0 — Research & Architecture

- inspect environment
- verify current stable tools
- verify provider docs
- document assumptions
- create architecture
- create ADRs
- create task backlog
- configure Claude project agents/skills appropriately

Do not spend the entire session planning.

Proceed into implementation.

## Phase 1 — Repository Foundation

- monorepo
- frontend
- API
- worker
- database
- Redis
- migrations
- Docker
- CI
- shared contracts
- configuration
- logging
- health checks

## Phase 2 — Player Data

- canonical player model
- Sleeper player sync
- nflverse ID crosswalk
- nflverse player metadata
- identity resolution
- admin diagnostics

## Phase 3 — Historical Data

- historical injuries
- weekly stats
- relevant depth charts
- workload features
- data-quality checks

## Phase 4 — Health Intelligence

- injury normalization
- heuristic risk model
- injury timeline
- health API
- health UI

## Phase 5 — Rankings

- ADP import
- projection import
- VORP
- positional scarcity
- tiering
- risk adjustment
- rankings UI

## Phase 6 — Draft Engine

- league config
- roster needs
- pick state
- scoring
- survival probability
- recommendation engine
- explanation engine

## Phase 7 — Mock Draft

- simulator
- deterministic seeds
- live UI events
- full War Room

## Phase 8 — Sleeper Live Draft

- league onboarding
- draft detection
- live poller
- Redis events
- SSE/WebSocket
- roster synchronization
- reconnect/staleness handling

## Phase 9 — ML

Only after reliable historical features exist.

- dataset generation
- leakage audit
- baseline
- candidate model
- temporal validation
- calibration
- model card
- guarded production inference

## Phase 10 — Product Polish

- comparison
- favorites
- command palette
- alerts
- settings
- accessibility
- animations
- responsive UX
- performance

## Phase 11 — Production Readiness

- full test suite
- load testing
- security review
- observability
- documentation
- deployment configuration
- final architectural review

---

# 40. LIVE DRAFT QUALITY BAR

Before declaring live draft functionality complete, prove with automated/integration testing that:

- new pick appears
- pick is persisted once
- drafted player disappears from available players
- selecting team's roster updates
- recommendation results change
- user next-pick calculation remains correct
- disconnected browser reconnects
- stale state is detected
- provider timeout doesn't crash application
- duplicate provider responses do not duplicate picks
- multiple picks received at once are processed in order

---

# 41. DEMO QUALITY BAR

A person cloning the GitHub repository must be able to experience the core product **without having a Sleeper account**.

Demo mode should provide:

- realistic player pool
- mock health scores
- ADP
- projections
- mock live draft
- recommendation changes
- player comparison
- injury timeline
- polished UI

This is critical for portfolio demonstrations.

---

# 42. ANALYTICS EXPLAINABILITY

Every important score should be decomposable.

For example:

```text
Overall Draft Score: 91.4

Projected Value             +31.0
Value Over Replacement      +20.5
Positional Scarcity         +12.1
Roster Need                 +10.4
ADP Value                   +14.2
Health Adjustment            -4.1
Bye/Roster Correlation       -0.8
Next Pick Urgency            +8.1
```

Exact math can differ.

The requirement is explainability.

Never present an opaque 91.4 with no reason.

---

# 43. PRODUCT SAFETY / LANGUAGE

Do not state:

"This player will get injured."

Use:

- elevated availability risk
- historical risk signal
- current health concern
- model-estimated availability
- uncertainty

Display model limitations.

Fantasy recommendations are probabilistic.

---

# 44. OPTIONAL FUTURE PROVIDERS

Architect for but do not fake integrations with:

- ESPN Fantasy
- Yahoo Fantasy
- NFL Fantasy
- paid sports data APIs

Use adapter interfaces.

Only implement an integration after verifying official API/access methods.

Sleeper is the live integration priority.

---

# 45. COST CONTROL

The default local application should not require paid infrastructure.

Prefer free/open data and local services.

External paid integrations should be optional.

The core War Room should not depend on an LLM API.

---

# 46. GIT PRACTICES

Use coherent commits when appropriate.

Do not commit:

- `.env`
- credentials
- provider dumps
- generated caches
- model training datasets too large for Git
- secrets
- node_modules
- Python virtual environments

Create a comprehensive `.gitignore`.

Use Git LFS only if genuinely necessary.

---

# 47. CRITICAL ANTI-HALLUCINATION RULE

Whenever you do not know something about an external provider:

**STOP GUESSING AND VERIFY.**

Examples:

- endpoint path
- API parameter
- injury field
- rate limit
- current NFL season data availability
- authentication method
- licensing rule
- response schema

If documentation cannot confirm it:

- label it unknown
- design an interface
- use a local fixture/demo implementation
- leave the production adapter disabled
- document what is required

Do not silently invent it.

---

# 48. FINAL ACCEPTANCE CRITERIA

The project is successful when I can:

1. Clone it.
2. Start it locally.
3. Enter Demo Mode.
4. Watch a realistic mock draft occur live.
5. See drafted players disappear.
6. See my roster update.
7. See live recommendations change.
8. Inspect player health/injury intelligence.
9. Compare players.
10. Understand why the system recommends a player.
11. Import ADP/projection data.
12. Connect a real Sleeper account.
13. Select a Sleeper league.
14. Select its draft.
15. Follow the draft live.
16. Receive updated recommendations as picks occur.
17. Recover gracefully from temporary provider failures.
18. Run automated tests successfully.
19. Read documentation that accurately explains the real implementation.
20. Review a clean, professional GitHub repository suitable for a senior technical interview.

---

# 49. FIRST ACTION

Begin now.

Do not respond with a generic architecture essay.

Perform the following:

1. Inspect the current repository/environment.
2. Inspect available Claude Code capabilities.
3. Verify the latest official documentation for the technologies/data providers you intend to use.
4. Create the initial architecture and decision records.
5. Create the Claude project configuration/subagents/skills that will materially improve implementation.
6. Create the monorepo foundation.
7. Run it.
8. Test it.
9. Continue through the implementation phases.

Maintain a concise running progress log.

Whenever you encounter a decision:

- research it
- choose the most defensible option
- record the reasoning
- proceed

Whenever you find something broken:

- investigate root cause
- fix it
- add regression coverage

Do not lower the quality bar just to finish faster.

**Build Fantasy Health Edge.**