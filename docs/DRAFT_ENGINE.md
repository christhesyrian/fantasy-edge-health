# The draft engine

How a recommendation is produced, and why it is built this way.

Source: [`src/fhe/core/draft/`](../src/fhe/core/draft/). Everything here is pure —
no database, no network, no clock — so every claim below is reproducible from a
unit test.

---

## The shape of the answer

```json
{
  "player_uuid": "syn-rb-004",
  "name": "Josiah Donnelly",
  "overall_score": 71.0,
  "model_rank": 1,
  "market_adp": 10.0,
  "adp_value": 9.0,
  "health_risk": 17,
  "next_pick_survival_probability": 0.0,
  "recommendation": "DRAFT_NOW",
  "components": [
    { "label": "Value over replacement", "points": 35.9, "detail": "+176.0 projected points above the RB29 baseline." },
    { "label": "Positional scarcity",    "points": 12.0, "detail": "1 left in the top RB tier, about 4.7 expected to go before your next pick." },
    { "label": "Roster need",            "points": 12.0, "detail": "Fills an open starting slot for RB." },
    { "label": "Next-pick urgency",      "points":  9.0, "detail": "100% chance he is gone before your next pick (#20)." },
    { "label": "Availability risk",      "points": -3.4, "detail": "Availability risk 17/100 (low); estimated 79% of games available." }
  ]
}
```

The components sum to `overall_score`. That is a hard invariant, asserted by
tests at both the domain and API levels.

---

## Why additive

The score is a weighted sum, not a product or a learned blend. This is a product
decision that outranks marginal accuracy.

A manager has seconds to decide whether to trust a number. An additive score can
always be shown as the arithmetic that produced it — "35.9 because he is 176
points above replacement, 12 because your RB slot is empty". A multiplicative or
fitted model might rank slightly better and would be impossible to defend at the
table.

The place for a non-linear fit is the availability model, where the output is a
single calibrated probability that then enters this sum as one term.

---

## The terms

```
overall = w_vorp     × vorp_normalised
        + w_scarcity × positional_scarcity
        + w_need     × roster_need
        + w_adp      × adp_value_normalised
        + w_urgency  × take_now_probability × vorp_normalised
        − w_health   × availability_risk
        − w_bye      × bye_collision
        − w_late     × late_round_position
```

| Weight | Value | Reasoning |
| --- | --- | --- |
| `W_VORP` | 40 | Talent above replacement dominates. Everything else is an adjustment to it. |
| `W_ADP_VALUE` | 15 | Market disagreement is real signal but should not outrank talent. |
| `W_SCARCITY` | 12 | Matters at a tier cliff, noise otherwise. |
| `W_ROSTER_NEED` | 12 | Enough to break a tie, not enough to force a reach. |
| `W_URGENCY` | 10 | Scaled by value, so a replacement-level player about to be taken is not urgent. |
| `W_HEALTH` | 20 | A severe-risk player loses a fifth of the maximum score. |
| `W_LATE_ROUND_DISCOUNT` | 30 | Large enough that no kicker outranks a startable skill player until the closing rounds. |
| `W_BYE_COLLISION` | 4 | A real but minor consideration. |

Weights live as named constants in
[`engine.py`](../src/fhe/core/draft/engine.py) with the reasoning beside them.

---

## Value over replacement

A raw projection is not a draft signal: 280 points is elite for a tight end and
replacement-level for a quarterback, and the difference is a property of the
*league*, not the player.

Replacement rank is `team_count × (dedicated starters + expected flex share)`.
Flex slots are split across eligible positions in proportion to their dedicated
starting slots — in a 2RB/2WR/1TE/1FLEX league that sends 40% of the flex to RB,
40% to WR and 20% to TE, which matches how flex spots are actually filled far
better than an even three-way split.

`SUPER_FLEX` is handled separately, with 85% assigned to quarterbacks. Splitting
it proportionally would put QB replacement around QB14 in a 12-team superflex —
far too shallow, and systematically under-valuing quarterbacks in exactly the
format where they matter most.

Resulting baselines:

| League | QB | RB | WR | TE |
| --- | --- | --- | --- | --- |
| 12-team, 1QB, 1 flex | 12 | 29 | 29 | 14 |
| 12-team superflex | 22 | 30 | 30 | 15 |
| 12-team 2QB | 24 | 29 | 29 | 14 |
| 10-team, 3WR | 10 | 23 | 35 | 12 |

These match conventional fantasy baselines, which is the point: an engine that
disagrees with well-understood arithmetic is wrong, not clever.

### Two traps, both hit during development

**Every rosterable position needs a baseline.** Kickers and defenses were
initially excluded from the valued-position set, so they had no replacement
level, so their value over replacement equalled their *entire projection* — and
a kicker ranked third overall. Pinned by
`test_kickers_and_defenses_are_not_drafted_early`.

**The normalisation scale must be fixed before the draft.** Normalising VORP
against the best *currently available* player means the best remaining player
always scores the full value weight — so a round-14 defense scored like the 1.01.
The scale is now the pre-draft maximum, held on `ReplacementBaseline.max_vorp`.

---

## Positional scarcity and tiers

A tier boundary is placed where the projection drop to the next player is both
larger than a floor and more than twice the median drop for that position. The
threshold derives from the pool's own gap distribution, so it adapts to scoring
format and projection scale instead of hard-coding a points value.

`scarcity_index` is the share of the current top tier expected to disappear
before your next pick — the only form of scarcity that changes a decision. An
index of 1.0 means the tier will not survive your wait.

---

## Next-pick survival probability

The flagship signal. Draft position is modelled as
`D ~ Normal(mu = adp, sigma)`, and the quantity needed is *conditional* on the
player being demonstrably available right now:

```
P(D ≥ n | D ≥ c) = P(D ≥ n) / P(D ≥ c)
```

Conditioning matters. A player with ADP 20 still on the board at pick 40 is
behaving nothing like their ADP, and the unconditional formula reports a
near-zero survival probability that is obviously wrong to anyone looking at the
screen.

### Re-anchoring fallers

Even conditioned, the stale mean gives absurd answers: a player 25 picks past
their ADP came out at ~0.01% likely to last another twelve. In reality players
who fall tend to keep falling — something has changed how the room values them.

So when the current pick has passed a player's ADP, the distribution re-centres
on the current pick, which is the market's revealed lower bound on where they
will go, with dispersion widened to match. The same player now reads ~41%.

When the source publishes no dispersion, sigma is estimated as
`clamp(adp × 0.32, 3, 40)`: uncertainty about a draft slot grows roughly in
proportion to how late it is.

**This is an approximation.** Real draft-position distributions are
right-skewed — a player can fall a long way but cannot be taken before pick 1.
Normality needs only a location and a scale, both of which real ADP sources
publish, which makes it a defensible first model. It should be replaced by an
empirical distribution once simulated or observed draft data exists, not tuned.

---

## Roster need

Computed by actually filling the league's declared lineup with the players a
team holds, then asking what is left.

Slots fill most-restrictive-first. That ordering is load-bearing: filling a FLEX
before a dedicated RB slot could strand a running back in the flex and report a
phantom RB need. Need drops from 1.0 (an unfilled dedicated slot) to 0.55 (only
flex remains) to 0.15 (depth), and collapses to 0.05 once a team is genuinely
stacked at a position.

---

## Resolving the ADP/rank circularity

`adp_value` is `market_adp − model_rank`, but `model_rank` is an output of the
score that `adp_value` feeds. The engine breaks the loop with an explicit
two-pass evaluation:

1. Score every player **without** the ADP term to get a provisional rank.
2. Compute `adp_value` against that rank and re-score.

Stated plainly rather than hidden, because it is the first question a reader
asks on seeing both quantities.

---

## Late-round positions

Kickers and defenses carry a large negative adjustment until the user's
remaining picks approach the number of such slots still to fill. Without it they
rank absurdly early: tightly clustered projections give them a small but real
value over replacement, which combines with an unfilled dedicated roster slot and
a large apparent ADP discount.

Verified behaviour across a full draft:

| Phase | Best kicker/defense rank |
| --- | --- |
| Round 1 | 255 |
| Mid draft | 139 |
| Final two rounds | 1 |

---

## Verdict labels

Assigned in order, because risk vetoes value and urgency beats patience:

| Label | Condition |
| --- | --- |
| `AVOID` | availability risk ≥ 70 |
| `DISCOUNT_RISK` | availability risk ≥ 45 |
| `REACH` | market drafts him ≥ 15 picks *earlier* than the model ranks him |
| `DRAFT_NOW` | top-ranked and unlikely to survive |
| `STRONG_VALUE` | market drafts him ≥ 12 picks later than the model ranks him |
| `LIKELY_AVAILABLE_LATER` | survival probability ≥ 0.75 |

---

## Determinism

Identical input always produces identical output — same order, same scores.
Ties break on ADP, then on player id, so the board never shuffles between
recomputes. Asserted by `test_identical_input_produces_identical_output`.

This is what makes the seeded simulator a genuine regression test rather than
only a demo.

---

## What this engine does not do

- **No auction values.** Auction drafts have no pick order, and the survival
  model has no meaning without one.
- **No dynasty or keeper valuation.** Multi-year value is a different model.
- **No stacking or correlation.** Real, and not attempted.
- **No opponent modelling in the live path.** The simulator models opponents;
  the live engine reasons from ADP, which is the aggregate of real behaviour.
