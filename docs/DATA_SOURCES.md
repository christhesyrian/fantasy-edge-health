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

## FantasyPros

**Licensed, not public.** This is the only source here with terms that restrict
what may be stored and how often it may be called, so those terms are
implemented in code rather than left to whoever runs it to remember.

| | |
| --- | --- |
| **Purpose** | Season projections and consensus ADP — the two inputs the engine cannot derive and will not invent. |
| **Docs** | <https://api.fantasypros.com/public/v2/docs> (spec: `docs/fantasypros_v2_public.yml`) |
| **Base URL** | `https://api.fantasypros.com/public/v2/json` |
| **Auth** | **API key**, sent as the `x-api-key` header. Personal, and must be kept confidential. |
| **Adapter** | [`src/fhe/data/providers/fantasypros.py`](../src/fhe/data/providers/fantasypros.py) |
| **CSV converter** | [`src/fhe/data/ingest/fantasypros_csv.py`](../src/fhe/data/ingest/fantasypros_csv.py) |
| **Verified** | 2026-08-23, against the published OpenAPI spec |

### Endpoints used, and one deliberately not used

| Operation | Path |
| --- | --- |
| Season projections | `GET /nfl/{season}/projections?position=&scoring=` |
| Consensus ADP | `GET /nfl/{season}/consensus-rankings?position=&type=ADP&scoring=` |
| ~~Historical points~~ | `GET /nfl/{season}/player-points` — **not implemented on purpose** |

The Terms of Use state the licence does **not** cover "Data that constitutes
historical player statistics", and require that any such data received be
deleted promptly. The simplest way to honour that is never to request it:
`player-points` has no client method, and no historical statistic from this
provider is stored anywhere. Workload history continues to come from nflverse,
which is public data under its own licence.

### Terms enforced in code

| Term | Where |
| --- | --- |
| "one API call per second" | `fantasypros_min_seconds_between_calls`, measured from the previous call. |
| "up to 100 API calls per day" | `_DailyBudget`, **persisted to disk** so a restart cannot silently reset the count and take the account over its licence. |
| "cache data on your end" | Every response cached for `fantasypros_cache_hours` (12h). A cached read is checked *before* the quota, so cached data keeps working once the day's calls are spent. |
| "keep your API key strictly confidential" | Read from `FHE_FANTASYPROS_API_KEY`, `repr=False` on the setting, never logged, never written to cache, never in an error message. |
| Attribution | Every imported value is stamped `FantasyPros`, which the war room displays beside the number. |
| Personal, non-commercial | See the note below. |

### The free tier returns 10 rows per call — measured, not assumed

Verified on 2026-08-23 with a real key. Both endpoints answer with
`"tier": "free"`, `"public_api_limited": true`, and `"limit": 10`, alongside a
`count` of the full result set:

| Call | Returned | Available (`count`) |
| --- | ---: | ---: |
| `projections?position=QB` | 10 | 83 |
| `consensus-rankings?type=ADP&position=ALL` | 10 | 660 |

So the API alone **cannot fill a draft board**. Six positions at ten rows each
is sixty players, against the ~200 a twelve-team draft consumes. The adapter
logs `fantasypros_response_truncated_by_tier` on every such response, because
importing a tenth of a board silently is the worst available outcome: it looks
populated and runs out mid-draft.

Two consequences worth knowing:

- **The CSV export is the real path to a full board.** A FantasyPros account
  can export the complete projection and ranking sets from the website; the
  converter turns either into import shape.
- **`rank_std` is 0.0 on this tier**, because the free consensus is built from
  five experts. The survival model therefore falls back to its own dispersion
  assumption rather than using a real one — which is the correct behaviour, but
  it is a fallback and is labelled as such.

### Two things a reader should know

**The `stats` object shape is not in the published spec**, so it was read from
a real response. It carries `points`, `points_ppr`, and `points_half` side by
side, and the adapter selects by scoring format — which matters, because the
endpoint echoes `"scoring": "STD"` even when PPR is requested. Trusting the
request parameter would have silently loaded standard-scoring projections into
a PPR league. Where no recognisable points field exists the projection is
recorded as **unknown** rather than guessed, so missing data lowers confidence
instead of inventing value.

Likewise the ADP response carries `rank_ave`, `rank_min`, `rank_max`, and
`rank_std`, none of them in the published schema. The average is used rather
than `rank_ecr`, which is a consensus *rank* and not an average draft position.

**Non-commercial and non-compete.** The licence grants use of the Data "for
personal, non-commercial purposes only", and separately forbids using it to
build anything competing with a FantasyPros product. A personal draft assistant
run locally sits inside that grant. Publishing this application to others with
FantasyPros data in it would not, and no FantasyPros data is committed to this
repository.

### The CSV path

The converter exists because the API is not the only way in, and a rate-limited
key is a poor fit for iterating on a draft board. Export projections or rankings
from FantasyPros yourself, then:

```bash
./.venv/bin/python -m fhe.cli convert fantasypros --kind projections \
  --in ~/Downloads/FantasyPros_Projections.csv --out data/imports/projections.csv
```

Column headings differ between their exports and change over time, so the
converter matches by alias and **reports which source column filled each field**.
When a required column cannot be found it lists every heading present, so the
fix is one glance rather than a guess.

---

## nflverse — schedules

Added for fantasy-playoff strength of schedule.

| | |
| --- | --- |
| **Asset** | `schedules/games.parquet` (a CSV twin exists at the same tag) |
| **Verified** | 2026-09-03: 7,548 games, including all 272 of the 2026 regular season |
| **Adapter** | `NflverseProvider.get_schedules` |
| **Ingestion** | `fhe ingest schedule --seasons 2025,2026` |

Not season-scoped, unlike every other nflverse reader here: the release ships
one file covering every year, and the upcoming season appears in it as soon as
the league publishes fixtures — with scores still null, which is exactly the
state a draft needs.

**The other half needs no provider at all.** Defensive strength is measured from
this system's own `player_weekly_stats`, because every stat line records the
opponent it was produced against. "Points allowed to running backs" is therefore
a `GROUP BY` over data already ingested, not a new source to verify. Measured
per position rather than overall, since a defence can be stingy against the run
and generous to receivers.

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

### Identifier enrichment

Identifiers are harvested from **both** the crosswalk and nflverse's own player
table, rather than stopping once a player is resolved. Returning early on a
direct `gsis_id` match used to discard everything else, which mattered
concretely: `pfr_id` coverage sat at 37% and snap counts — the one dataset keyed
on it — could not join for 2,336 in-scope players in a single season.

Merging both sources lifted `pfr_id` coverage to **69.9%** and cut unresolved
snap-count rows from 2,336 to **38**.

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

| Dataset | Tag | Asset | Seasons available |
| --- | --- | --- | --- |
| Injuries | `injuries` | `injuries_{season}.parquet` | **2009 – 2025** |
| Snap counts | `snap_counts` | `snap_counts_{season}.parquet` | 2012 – 2025 |
| Depth charts | `depth_charts` | `depth_charts_{season}.parquet` | 2001 – 2026 |
| Rosters | `rosters` | `roster_{season}.parquet` | 1920 – 2026 |
| Weekly player stats | **`stats_player`** | `stats_player_week_{season}.parquet` | 1999 – 2025 |
| Players (identity) | `players` | `players.parquet` | rebuilt daily |

> **Weekly stats moved.** The legacy `player_stats` release still exists and still
> lists `player_stats_{season}.parquet` assets, but it stops before the current
> seasons — `player_stats_2025.parquet` returns **404**, while
> `stats_player/stats_player_week_2025.parquet` is present and complete (19,422
> rows). Reading the legacy tag would silently yield nothing for recent seasons.

> **Snap counts use a different join key.** They are keyed by
> **`pfr_player_id`**, not `gsis_id` like every other dataset here. Weekly stats
> use `player_id`, which despite the name holds a `gsis_id`.

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
