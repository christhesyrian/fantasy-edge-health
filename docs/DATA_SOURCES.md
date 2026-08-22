# Data sources

Every external source this project uses, what was **verified** about it rather
than assumed, and where it falls short.

The governing rule (directive §4, §47) is that no endpoint, field, cadence,
limit, or availability date is ever invented. Everything below was checked
against official documentation *and* against live responses. Where the two
disagree, the live behaviour is recorded and handled — several of them do.

**All entries verified 2026-08-22** unless noted. Re-verify before trusting an
integration after a long gap; the fixture files carry the same date so drift is
detectable.

---

## Sleeper

| | |
| --- | --- |
| **Purpose** | Live league, draft, roster, and player data. The primary live integration. |
| **Docs** | <https://docs.sleeper.com> |
| **Base URL** | `https://api.sleeper.app/v1` |
| **Auth** | **None.** The API is public and read-only. No key, no OAuth, no token. |
| **Adapter** | [`src/fhe/data/providers/sleeper.py`](../src/fhe/data/providers/sleeper.py) |
| **Contract tests** | [`tests/contract/test_sleeper_provider.py`](../tests/contract/test_sleeper_provider.py) |
| **Fixtures** | [`data/fixtures/sleeper/`](../data/fixtures/sleeper/) |

### Rate limits

The documentation states: *"A general rule is to stay under 1000 API calls per
minute, otherwise, you risk being IP-blocked."*

This client self-limits to **600 requests/minute** (`FHE_SLEEPER_MAX_RPM`) via a
token bucket. The margin is deliberate: being IP-blocked mid-draft is
unrecoverable within the session, and no amount of retry logic fixes it.

Live draft polling defaults to one request every **3 seconds**
(`FHE_DRAFT_POLL_INTERVAL_SECONDS`) — 20 requests/minute per active draft, far
inside the ceiling.

### Endpoints used

| Operation | Path |
| --- | --- |
| User lookup | `GET /user/{username_or_id}` |
| Leagues for a user | `GET /user/{user_id}/leagues/nfl/{season}` |
| League | `GET /league/{league_id}` |
| League users | `GET /league/{league_id}/users` |
| Rosters | `GET /league/{league_id}/rosters` |
| Drafts for a league | `GET /league/{league_id}/drafts` |
| Drafts for a user | `GET /user/{user_id}/drafts/nfl/{season}` |
| Draft | `GET /draft/{draft_id}` |
| Draft picks | `GET /draft/{draft_id}/picks` |
| Traded picks | `GET /draft/{draft_id}/traded_picks` |
| All NFL players | `GET /players/nfl` |
| Trending adds/drops | `GET /players/nfl/trending/{add\|drop}?lookback_hours=&limit=` |
| NFL state | `GET /state/nfl` |

### Where documentation and reality disagree

These were found by calling the API, not by reading about it. Each is handled in
the adapter and pinned by a contract test.

1. **`roster_id` on a draft pick is an integer, not a string.** The documented
   example shows `"roster_id": "1"`. Live responses return `1`. Both parse.
2. **Draft picks carry an undocumented `reactions` field.** Ignored rather than
   treated as a contract violation.
3. **`is_keeper` is `null`, not `false`,** when a pick is not a keeper.
4. **Not-found behaviour is inconsistent across endpoints:**
   - unknown **user** → HTTP **200** with a body of `null`
   - unknown **league** → HTTP **404** with a body of `null`
   - unknown **draft** or its picks → HTTP **404** with a body of `null`

   Lookups treat 404 as "no such resource" and return `None`, because failing to
   find a league during onboarding is a normal outcome. `get_draft_picks` is the
   deliberate exception: it lets a 404 raise, so a draft that has vanished can
   never be mistaken for a draft with no picks yet.
5. **`/players/nfl` is ~14.6 MB, not the ~5 MB the docs suggest,** and returns
   **12,221** players. The documentation asks callers to fetch it *"only once
   per day at most"*; the adapter caches it on disk for 20 hours and writes
   atomically so an interrupted fetch cannot leave a truncated cache.

### Injury fields: measured coverage

Counts are non-empty values across all 12,221 players in the live payload.

| Field | Populated | Verdict |
| --- | --- | --- |
| `injury_status` | 653 | Usable. Values: `Questionable`, `IR`, `NA`, `PUP`, `Sus`, `Out`, `DNR`, `Doubtful`, `COV`. |
| `injury_body_part` | 575 | Usable. Free text, normalised by the taxonomy. |
| `injury_notes` | 84 | Sparse but stored. |
| `injury_start_date` | 0 | **Field exists, always null.** Never fabricated. |
| `practice_participation` | **1** | **Effectively unavailable.** Not read. |
| `practice_description` | **1** | **Effectively unavailable.** Not read. |

> The directive asks for practice participation from Sleeper. It is populated
> for a single player out of twelve thousand, so practice data comes from
> nflverse instead. Reading a field this empty would produce a "practice
> trajectory" for almost nobody while implying the signal exists.

### Player identifier coverage

Measured over the 1,040 rostered QB/RB/WR/TE/K, and again over the top 200 by
Sleeper's own `search_rank`:

| Identifier | All rostered | Top 200 |
| --- | --- | --- |
| `sportradar_id` | 98.6% | 100% |
| `fantasy_data_id` | 99.3% | — |
| `rotowire_id` | 97.6% | — |
| `yahoo_id` | 24.1% | 29.0% |
| `espn_id` | 23.3% | 29.0% |
| **`gsis_id`** | **17.7%** | **21.0%** |

`gsis_id` is the key nflverse is built on, and Sleeper has it for a fifth of the
players that matter. That single measurement is why the crosswalk below exists.

---

## nflverse

| | |
| --- | --- |
| **Purpose** | Historical injuries, practice reports, weekly stats, snap counts, depth charts, and the player table. |
| **Docs** | <https://github.com/nflverse/nflverse-data> |
| **Auth** | None. Public GitHub Releases. |
| **URL pattern** | `https://github.com/nflverse/nflverse-data/releases/download/{tag}/{asset}` |
| **Adapter** | [`src/fhe/data/providers/nflverse.py`](../src/fhe/data/providers/nflverse.py) |
| **Licence** | Data released under permissive terms by the nflverse project; see their repository. Only cached locally, never redistributed here. |

Parquet is used throughout rather than CSV: it is typed, far smaller, and avoids
the string-coercion bugs that come free with CSV. **R is not required** — the
production path reads the published Parquet assets directly from Python.

### Coverage, verified by enumerating the releases API

| Dataset | Tag | Seasons available |
| --- | --- | --- |
| Injuries | `injuries` | **2009 – 2025** |
| Snap counts | `snap_counts` | 2012 – 2025 |
| Depth charts | `depth_charts` | 2001 – 2026 |
| Rosters | `rosters` | 1920 – 2026 |
| Weekly player stats | `player_stats` | 1999 – 2026 |
| Players (identity) | `players` | single file, rebuilt daily |

> **Correction to the build directive.** The directive states that nflverse
> injury data "currently ends after the 2024 season because the upstream injury
> source stopped providing data." That is **out of date**. `injuries_2025.parquet`
> exists, contains **6,068 rows across weeks 1–22** including the full
> postseason, and the release was rebuilt **2026-03-18**. There is no
> `injuries_2026.parquet`, which is expected: the 2026 season has not started.
>
> Requesting a season outside the verified range fails fast with a clear message
> rather than producing a confusing 404 from GitHub.

### Injuries schema

`season`, `game_type`, `team`, `week`, `gsis_id`, `position`, `full_name`,
`first_name`, `last_name`, `report_primary_injury`, `report_secondary_injury`,
`report_status`, `practice_primary_injury`, `practice_secondary_injury`,
`practice_status`, `date_modified`.

### Known dirt, handled

- `practice_status` contains literal `"\n    "` padding rows. These must
  normalise to `UNKNOWN`; reading them as participation would invent a practice
  report for thousands of player-weeks.
- Both `practice_status` and `report_status` contain the value `"Note"`, which
  is an informational marker carrying no game status.
- Body-part text includes laterality prefixes (`"right Shoulder"`), inconsistent
  casing (`"Right Shoulder"` vs `"right Shoulder"`), plurals (`"Ribs"` / `"Rib"`),
  truncation (`"Not injury related - resting p"`), and comma-separated
  multi-part descriptors (`"back, ankle, knee"`).

The taxonomy in [`src/fhe/core/injury/taxonomy.py`](../src/fhe/core/injury/taxonomy.py)
maps **99.97%** of 62,915 observations across 2019–2025 to a controlled body
region. The remaining unmapped values (`"Cramps"`, `"Other"`, `"--"`) are
genuinely not body regions, and are recorded as such rather than forced into a
bucket. The coverage floor is asserted by a test.

### `players.parquet` identifiers

Carries `gsis_id`, `esb_id`, `nfl_id`, `pfr_id`, `pff_id`, `otc_id`, `espn_id`,
`smart_id` across 25,050 players — but **no `sleeper_id`**. Combined with
Sleeper's 21% `gsis_id` coverage, this means the two sources cannot be joined
directly for four fifths of the relevant player pool.

---

## DynastyProcess player id crosswalk

| | |
| --- | --- |
| **Purpose** | Bridge Sleeper player ids to `gsis_id`, making nflverse history reachable. |
| **Source** | <https://github.com/dynastyprocess/data> — `files/db_playerids.csv` |
| **Auth** | None. |
| **Cadence** | Regenerated weekly by the upstream project's CI. |
| **Licence** | **GPL-3.0.** |
| **Loader** | [`src/fhe/data/ingest/crosswalk.py`](../src/fhe/data/ingest/crosswalk.py) |

> **Licensing.** This project is MIT; the crosswalk is GPL-3.0. It is therefore
> **downloaded at runtime into a git-ignored cache and never redistributed** as
> part of this repository. The resolver degrades gracefully without it — it just
> links far fewer players, and the ingestion run says so.

### Why it is worth the dependency

Measured against the live Sleeper payload and nflverse `players.parquet`:

| Pool | Resolved to an nflverse anchor | Conflicts |
| --- | --- | --- |
| Top 200 by search rank | **100%** | 0 |
| Top 400 | 99.2% | 2 |
| All 1,038 rostered skill players | 97.1% | 8 |

Without it, direct `gsis_id` joining reaches about 21%.

### Known dirt, handled

- **Missing values are the literal string `"NA"`** (an R convention). Treating
  that as data creates one enormous fictional player whose id is `"NA"`, and
  silently corrupts every join. `clean_token()` handles it, and tests pin it.
- The file contains a small number of **genuine errors where one provider id is
  claimed by two different players** — 3 collisions across 24,441 id pairs, for
  example a `stats_id` shared by John Metchie and Dameon Pierce. An external
  identifier must identify exactly one player, so ingestion resolves the
  collision deterministically by identity confidence and reports the loser as a
  rejection rather than crashing or silently overwriting.

---

## Manual CSV import (ADP and projections)

| | |
| --- | --- |
| **Purpose** | ADP and projections without a paid API. |
| **Schema** | [`data/schemas/README.md`](../data/schemas/README.md) |
| **Importer** | [`src/fhe/data/ingest/csv_import.py`](../src/fhe/data/ingest/csv_import.py) |
| **Endpoints** | `POST /api/v1/imports/adp`, `POST /api/v1/imports/projections` |

The directive forbids scraping FantasyPros, ESPN, Yahoo, Rotowire, or any other
provider in violation of their terms, and **no free licensed projection API has
been verified**. So the supported route is a file the user is already licensed
to have. Every imported value keeps the `source` name supplied at import time,
and the war room displays it beside the number.

Guards, because this is the only ingestion path fed by an uploaded file: size
capped before decoding, row count capped, value ranges enforced, nothing in the
file evaluated, and ambiguous player matches rejected rather than guessed.

---

## Providers deliberately NOT implemented

Architected for via adapter interfaces, but **not built and not faked**:

| Provider | Why not |
| --- | --- |
| ESPN Fantasy | No verified public API contract or terms permitting this use. |
| Yahoo Fantasy | Requires OAuth app registration; not verified. |
| NFL Fantasy | No verified public API. |
| Paid sports data APIs | Optional by design; the product must work without them. |

The onboarding screen shows Sleeper connection as genuinely unavailable rather
than as a button that fails. An integration that does not exist should say so.

---

## Re-verification checklist

Run this when returning to the project after a long gap:

```bash
# Sleeper: endpoints, sizes, and not-found behaviour
curl -s https://api.sleeper.app/v1/state/nfl
curl -s -o /dev/null -w '%{size_download}\n' https://api.sleeper.app/v1/players/nfl
curl -s -o /dev/null -w '%{http_code}\n' https://api.sleeper.app/v1/league/000000000000000000

# nflverse: which injury seasons exist now
curl -s 'https://api.github.com/repos/nflverse/nflverse-data/releases?per_page=100' \
  | python3 -c "import json,sys; rs=json.load(sys.stdin); print(sorted(a['name'] for r in rs if r['tag_name']=='injuries' for a in r['assets'] if a['name'].endswith('.parquet')))"
```

Then run the contract tests, which will fail loudly if a payload shape moved:

```bash
./.venv/bin/python -m pytest tests/contract -q
```
