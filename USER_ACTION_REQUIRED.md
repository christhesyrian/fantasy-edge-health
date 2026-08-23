# Actions only you can perform

Nothing here blocks the product. Demo mode runs with no credentials, no
database, and no paid services, and the war room is fully usable that way.
Every item below unlocks an *additional* capability.

Each entry says whether it is **REQUIRED** or **OPTIONAL**, why, exactly what to
do, and what it unlocks.

---

## 1. Free disk space, then verify the Docker stack — REQUIRED to finish
   Compose verification

**Why:** Docker Desktop was started on 2026-08-23 and `docker compose build`
was run for the first time. It got further than ever before and then stopped
for a reason that has nothing to do with this project: **the disk is full.**

```
/System/Volumes/Data   228Gi total   981Mi free   100% capacity
```

BuildKit could not write `/var/lib/docker/buildkit/metadata_v2.db`
(`input/output error`), and the daemon then stopped responding entirely. Docker
itself is holding about 6.9 GB.

**What that run did establish:** the `web` image was genuinely broken and is now
fixed. `npm ci` failed with `EUSAGE` because `apps/web/package-lock.json` was
missing `@playwright/test`, `playwright`, `playwright-core`, and `fsevents`.
That is repaired, and `npm ci --dry-run` now resolves cleanly. The `postgres`,
`redis`, and `minio` images pulled; the API and worker images were building when
the disk gave out.

**What is still unverified:** the stack has never come *up*. No migration has
run in a container, no health endpoint has been checked against PostgreSQL, and
no SSE update has been exercised through it.

**What to do:** free several GB — the usual suspects are `~/Library/Caches`,
old Xcode simulators (`xcrun simctl delete unavailable`), and Docker's own
data. Then restart Docker Desktop and confirm it answers:

```bash
docker info --format '{{.ServerVersion}}'
```

Reclaim Docker's own space first, since it needs no judgement about your files:

```bash
docker builder prune -af && docker image prune -af
```

Then bring the stack up and exercise it:

```bash
docker compose up --build
```

Check `http://localhost:8000/api/v1/health/ready` reports ready with an
**empty** `degradations` array — that is the proof it is on PostgreSQL and Redis
rather than the SQLite and in-process fallbacks.

**Unlocks:** a production-shaped local stack, the first real verification of the
container images, and the only way to get PostgreSQL performance numbers;
everything in `docs/PERFORMANCE.md` is currently SQLite.

---

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
