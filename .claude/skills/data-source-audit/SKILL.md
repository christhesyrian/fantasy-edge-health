---
name: data-source-audit
description: Re-verify every external data provider against live behaviour and update the documentation. Use after a long gap, when a contract test fails, or before trusting an integration again.
---

# Data source audit

Confirms that what the code believes about external providers is still true.
Nothing here is taken on trust, including this project's own documentation.

## 1. Check the live providers

```bash
# Sleeper: state, payload size, and the inconsistent not-found behaviour
curl -s https://api.sleeper.app/v1/state/nfl
curl -s -o /dev/null -w 'players payload: %{size_download} bytes\n' \
  https://api.sleeper.app/v1/players/nfl
curl -s -o /dev/null -w 'unknown user:   HTTP %{http_code}\n' \
  https://api.sleeper.app/v1/user/zzz_not_a_real_user_91827
curl -s -o /dev/null -w 'unknown league: HTTP %{http_code}\n' \
  https://api.sleeper.app/v1/league/000000000000000000

# nflverse: which injury seasons exist right now
curl -s 'https://api.github.com/repos/nflverse/nflverse-data/releases?per_page=100' \
  | python3 -c "import json,sys; rs=json.load(sys.stdin); print(sorted(a['name'] for r in rs if r['tag_name']=='injuries' for a in r['assets'] if a['name'].endswith('.parquet')))"
```

## 2. Measure coverage, do not assume it

Field presence is the thing that silently changes. Count it:

```bash
./.venv/bin/python -c "
import json, collections
d = json.load(open('data/cache/sleeper_players.json'))['players']
c = collections.Counter()
for v in d.values():
    for k, val in v.items():
        if val not in (None, '', [], {}): c[k] += 1
for field in ('gsis_id','espn_id','injury_status','practice_participation'):
    print(f'{field:<24} {c[field]:>6} / {len(d)}')
"
```

A field populated for a handful of records is unavailable, whatever the schema
says. Record the measurement, not an impression.

## 3. Run the contract tests

```bash
./.venv/bin/python -m pytest tests/contract -q
```

These run against saved fixtures, so they pass even when a provider has changed.
That is the point — they pin *our* parsing. Compare the fixtures against the
live shapes from step 1 to catch drift the tests cannot see.

## 4. Update the record

Amend `docs/DATA_SOURCES.md`: the verification date, anything that moved, and
any new mismatch between documentation and live behaviour. Update the
`_verified_on` markers in `data/fixtures/`.

If something can no longer be confirmed, say so explicitly and disable the
adapter rather than leaving code that depends on a guess.
