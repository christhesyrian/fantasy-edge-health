# Actions only you can perform

Nothing here blocks the demo. The application is designed to run with no
credentials and no paid services; every item below unlocks an *additional*
capability.

---

## 1. Start Docker Desktop — needed for PostgreSQL and Redis

**Status:** blocking only the production-shaped local stack.
**Why:** the Docker daemon is not running on this machine, so `docker compose`
cannot start Postgres, Redis, or MinIO. Until then the app falls back to a local
SQLite file and an in-process event bus. Both fallbacks work, and both are
reported at startup and by `/health` so they can never be mistaken for a real
deployment.

**What to do:** launch Docker Desktop from Applications (or Spotlight), wait for
the whale icon to settle, then confirm:

```bash
docker info --format '{{.ServerVersion}}'
```

Once `docker-compose.yml` exists (Phase G), bring the stack up with:

```bash
docker compose up --build
```

---

## 2. Connect a real Sleeper league — needed for live draft mode

**Status:** not blocking. Demo mode covers the full product without it.
**Why:** Sleeper's API is public and needs no API key, but it does need *your
username* to find your leagues and drafts. Nothing is fabricated in its absence:
without a connected league the live-draft screens stay disabled rather than
showing invented data.

**What to do:** once onboarding ships (Phase C/E), enter your Sleeper username.
No password, no OAuth, no token — the API is read-only and unauthenticated.

To check your account resolves right now:

```bash
curl -s https://api.sleeper.app/v1/user/YOUR_SLEEPER_USERNAME
```

A JSON object means you are good. A literal `null` means the username is wrong
(Sleeper returns HTTP 200 with `null` for unknown users).

---

## 3. Supply ADP and projection data — optional, improves ranking quality

**Status:** not blocking. Synthetic projections drive demo mode.
**Why:** the directive forbids scraping FantasyPros, ESPN, Yahoo, or Rotowire in
violation of their terms, and no free licensed projection API has been verified.
So the system takes a **CSV import** path instead, and shows the provider and
timestamp beside every number it derives from your file.

**What to do:** once the importer ships (Phase A), export ADP/projections from a
source you are licensed to use and drop the CSV in `data/imports/`. The expected
column schema will be published in `data/schemas/`.

---

## 4. Anthropic API key — optional, for the natural-language assistant only

**Status:** not blocking, and deliberately never required.
**Why:** the directive (§25, §45) requires the war room to remain fully useful
with no LLM. The deterministic engine is authoritative; an assistant would only
ever explain what the engine already computed.

**What to do:** if you want it later, put the key in `.env` as
`FHE_ANTHROPIC_API_KEY`. `.env` is gitignored. Never commit it.

---

## 5. Install pnpm — optional, only if you prefer it to npm

`pnpm` is not on this machine. The frontend will use npm unless you install it:

```bash
brew install pnpm
```
