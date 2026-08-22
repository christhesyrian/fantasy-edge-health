# Security

Threat model and the decisions that follow from it.

---

## What this system is

A read-mostly analytics application with **no user accounts, no authentication,
and no personal data**. It reads public APIs, accepts CSV uploads from its
operator, and renders analysis. Sleeper's API is public and unauthenticated, so
the system holds no provider credentials either.

That shape removes whole categories of risk — no session fixation, no password
storage, no PII breach — and concentrates what remains in three places: the file
upload path, the database boundary, and the browser boundary.

---

## Assets worth protecting

| Asset | Why | Exposure |
| --- | --- | --- |
| The host process | Code execution via an upload or dependency | Highest |
| Database integrity | Corrupt data silently produces wrong advice | High |
| Operator's ADP/projection data | Possibly licensed, possibly paid for | Medium |
| Optional LLM API key | Costs money if leaked | Medium |
| Availability during a draft | The one moment the product matters | High |

---

## Threats and controls

### 1. Malicious CSV upload

The only ingestion path fed by an uploaded file, and therefore the sharpest edge.

- Size capped at 8 MB **against the raw bytes, before decoding**, so an
  oversized upload cannot be expanded into memory by the decode itself.
- Row count capped at 20,000.
- Parsed with the standard `csv` module. **Nothing in the file is evaluated** —
  no formula interpretation, no `eval`, no dynamic import.
- Every value range-checked; out-of-bounds numbers are rejected, not stored.
- Rejections are counted and sampled rather than silently dropped, so a
  malformed file is visible rather than partially applied.

Not a formula-injection vector, because no cell is ever written back out to a
spreadsheet.

### 2. SQL injection

All access goes through SQLAlchemy 2 with bound parameters. No query is built by
string concatenation. Upserts use dialect `ON CONFLICT` constructs rather than
read-modify-write, which also removes a lost-update race.

### 3. Cross-origin abuse

CORS origins are explicit and never wildcarded. `Settings.cors_origin_list`
parses a comma-separated list; a wildcard alongside `allow_credentials` would let
any site read authenticated responses, so the code has no path that produces one.

### 4. Secret exposure

- `.env` is git-ignored; `.env.example` carries no real values.
- No credential appears in source, and `alembic.ini` deliberately holds an empty
  `sqlalchemy.url` — asserted by a test.
- Structured logging runs a redaction processor over credential-shaped keys
  (`api_key`, `password`, `secret`, `token`, `authorization`) before rendering.
- **No secret is ever placed behind `NEXT_PUBLIC_`.** That prefix compiles the
  value into the client bundle.
- Pre-commit runs gitleaks plus local hooks that refuse to commit a `.env` file
  or the GPL-licensed crosswalk. CI runs gitleaks over full history.

### 5. Dependency compromise

- Pinned lower bounds; `package-lock.json` committed.
- CI runs `pip-audit --strict` and `npm audit --audit-level=high`.
- Dependabot groups routine patches into one PR and raises majors individually,
  so real attention goes to the changes that need it.
- Container images are multi-stage: the runtime layer carries no build toolchain.

### 6. Denial of service

- Expensive endpoints are bounded by construction: board depth capped at 500,
  comparison capped at 4 players, simulation registry capped at 200 sessions with
  LRU eviction.
- The recommendation engine is bounded by pool size, which is bounded by
  ingestion.
- **Not implemented:** per-client rate limiting. Acceptable for a
  single-operator deployment, and a prerequisite for a public one. Recorded
  below as a known gap.

### 7. Being blocked by a provider

A self-inflicted availability risk, and the one most likely to bite. The client
self-limits to 600 requests/minute against a documented 1000 ceiling, polls at
3-second intervals, backs off with jitter, and caches the 15 MB player payload
for 20 hours in line with the provider's stated guidance.

### 8. Container escape / privilege

Both images create an unprivileged user and run as it. No image needs root, so
none has it.

---

## What is deliberately not done

Naming these is more useful than implying a completeness that does not exist.

| Not implemented | Why | When it becomes necessary |
| --- | --- | --- |
| Authentication | No accounts, no per-user data | The moment a second user shares an instance |
| Per-client rate limiting | Single-operator deployment | Public exposure |
| CSRF protection | No cookie-based auth to forge | If session cookies are introduced |
| Audit logging | No privileged actions to audit | With authentication |
| Encryption at rest | No sensitive data stored | With user data |
| Content Security Policy headers | Not yet configured | Public deployment |

---

## Deployment checklist

Before exposing this beyond localhost:

- [ ] `FHE_ENV=production`, `FHE_LOG_FORMAT=json`
- [ ] `FHE_DATABASE_URL` set — production on the SQLite fallback is reported as
      a degradation for a reason
- [ ] `FHE_CORS_ORIGINS` set to exact origins, never `*`
- [ ] TLS terminated upstream; no plaintext transport
- [ ] Rate limiting added at the edge
- [ ] Security headers (CSP, HSTS, `X-Content-Type-Options`) at the edge
- [ ] `/api/v1/metrics` and `/api/v1/diagnostics/*` not publicly reachable
- [ ] Secrets from a secret manager, not environment files on disk
- [ ] Database backups configured and a restore actually tested

---

## Reporting

This is a portfolio project with no production deployment. A vulnerability found
here is best raised as a GitHub issue, or privately if it affects anyone running
their own instance.
