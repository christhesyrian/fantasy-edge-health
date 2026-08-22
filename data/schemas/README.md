# Import schemas

Fantasy Health Edge does not scrape FantasyPros, ESPN, Yahoo, Rotowire, or any
other provider, and no free licensed projection API has been verified. So ADP
and projections come in by **CSV import** from a source you are licensed to use.

Every imported number keeps the provider name and import timestamp you supply,
and the war room shows both beside the value.

## Matching players

Rows are matched to players in this order, strongest first:

1. `sleeper_id` or `gsis_id` column, if present — exact and unambiguous.
2. Normalised name + position + team.
3. Normalised name + position.

A row that matches more than one player is **rejected**, not guessed at. Rejected
rows are counted and sampled in the ingestion run so you can see exactly what
failed and why.

## `adp.csv`

| Column | Required | Notes |
| --- | --- | --- |
| `player_name` | yes | Any capitalisation; suffixes and punctuation are normalised. |
| `position` | yes | QB, RB, WR, TE, K, DEF (DST/D-ST accepted). |
| `adp` | yes | Average draft position, > 0. |
| `team` | no | Improves match confidence. |
| `adp_stdev` | no | Dispersion. Supplying it materially improves the next-pick survival model. |
| `min_pick` / `max_pick` | no | Observed range. |
| `sample_size` | no | Number of drafts behind the average. |
| `sleeper_id` / `gsis_id` | no | Exact match keys; use these when you have them. |

```csv
player_name,position,team,adp,adp_stdev,sample_size
Ja'Marr Chase,WR,CIN,1.8,0.9,4210
Bijan Robinson,RB,ATL,3.1,1.4,4210
```

## `projections.csv`

| Column | Required | Notes |
| --- | --- | --- |
| `player_name` | yes | |
| `position` | yes | |
| `projected_points` | yes | Season total unless `week` is supplied. |
| `team` | no | |
| `projected_points_low` / `projected_points_high` | no | Range, if your source publishes one. |
| `projected_games` | no | Expected games played. |
| `week` | no | Omit for a season projection, which is what a draft uses. |
| `sleeper_id` / `gsis_id` | no | Exact match keys. |

```csv
player_name,position,team,projected_points,projected_points_low,projected_points_high
Ja'Marr Chase,WR,CIN,312.4,241.0,388.5
Bijan Robinson,RB,ATL,298.1,219.7,356.2
```

## Bounds

Values outside these ranges are rejected rather than stored, because a
mis-parsed column is far more likely than a genuine outlier:

- `adp`: 0 < value <= 600
- `projected_points`: -50 <= value <= 700
- `week`: 1–23 when supplied
