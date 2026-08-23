# Actions only you can perform

Nothing here blocks the product. Demo mode runs with no credentials, no
database, and no paid services, and the war room is fully usable that way.
Every item below unlocks an *additional* capability.

Each entry says whether it is **REQUIRED** or **OPTIONAL**, why, exactly what to
do, and what it unlocks.

---

## 1. Nothing — the Docker stack is verified

Kept as a record rather than an action. On 2026-08-23 the full Compose stack was
built and run end to end for the first time: PostgreSQL, Redis, MinIO,
migrations, API, worker, and web. `/api/v1/health/ready` reported
`"database": "ok"`, `"event_bus": "redis"`, and an **empty** `degradations`
array — the proof it was on the real storage engines rather than the SQLite and
in-process fallbacks. A demo draft ran, server-sent events were delivered over
Redis with monotonic sequences, ingestion wrote 4,089 players into PostgreSQL,
and `docker compose down` shut everything down cleanly.

It found two real bugs, both fixed: an out-of-sync frontend lockfile that broke
`npm ci`, and a volume-ownership fault that made ingestion die with
`PermissionError` under the non-root container user.

```bash
docker compose up --build
```

## 2. Provide your Sleeper username — OPTIONAL

**Why:** Sleeper's API is public and read-only and needs **no API key, no
password, and no OAuth**. It does need your username to find your leagues and
drafts. Nothing is fabricated in its absence: with no connected league the live
screens stay disabled rather than showing invented data.

**What to do:** the onboarding UI is built and working. Start the app, open the
landing page, and enter your username in "Connect a Sleeper league". It resolves
your leagues, then your drafts, then connects.

To check your account resolves before you start:

```bash
curl -s https://api.sleeper.app/v1/user/YOUR_SLEEPER_USERNAME
```

A JSON object means you are good. A literal `null` means the username is wrong —
Sleeper returns HTTP 200 with `null` for unknown users.

**Note:** a live draft needs an ingested player pool first, or connecting fails
with a clear message. Run `./.venv/bin/python -m fhe.cli ingest players`.

**Unlocks:** live draft mode — a real board, following a real draft, with picks
arriving over SSE.

---

## 3. Supply ADP and projection data — OPTIONAL

**Why:** the directive forbids scraping FantasyPros, ESPN, Yahoo, or Rotowire in
violation of their terms, and no free licensed projection API could be verified.
So the system takes a **CSV import** path and shows the provider and timestamp
beside every number derived from your file.

**What to do:** the importer is built. Export ADP and projections from a source
you are licensed to use, then either drop the file in `data/imports/` or post it:

```bash
curl -F 'file=@your-adp.csv' -F 'source=your-source-name' \
  http://localhost:8000/api/v1/imports/adp
```

The expected columns are documented in `data/imports/README.md`.

**Unlocks:** rankings against real market data instead of synthetic demo values.
The engine works without it, but VORP and ADP value are only as good as their
inputs.

---

## 4. Deploy the backend — OPTIONAL, and only for a hosted frontend

**Why:** the frontend deploys to Vercel; the API cannot, because it holds
long-lived SSE connections and runs a polling loop between requests. A deployed
frontend in **live** mode needs a reachable API.

This is **not** needed to work on the UI: set
`NEXT_PUBLIC_PREVIEW_MODE=fixtures` and the frontend runs entirely offline
against recorded engine output.

**What to do:** follow [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — a container
platform that runs persistent processes, managed PostgreSQL, and
`FHE_CORS_ORIGINS` listing your frontend's exact origin. Then set
`NEXT_PUBLIC_API_BASE_URL` in the frontend deployment.

**Unlocks:** a hosted product with live drafts.

---

## 5. Anthropic API key — OPTIONAL, and deliberately never required

**Why:** the directive (§25, §45) requires the war room to remain fully useful
with no LLM. The deterministic engine is authoritative, and no language model
writes a recommendation or its reasons. An assistant could only ever explain
what the engine already computed.

**What to do:** if you want it later, put the key in `.env` as
`FHE_ANTHROPIC_API_KEY`. `.env` is git-ignored. Never commit it.

**Unlocks:** nothing that exists today. The assistant is not built.

---

## 6. Install pnpm — OPTIONAL

Not on this machine, and not needed: the frontend uses npm workspaces.

```bash
brew install pnpm
```

**Unlocks:** nothing. Preference only.
