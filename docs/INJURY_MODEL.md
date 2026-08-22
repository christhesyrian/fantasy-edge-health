# The health model

How availability risk is estimated, what it is built from, and — at length —
what it cannot tell you.

Source: [`src/fhe/core/health/`](../src/fhe/core/health/) and
[`src/fhe/core/injury/`](../src/fhe/core/injury/).

---

## What this is, precisely

This estimates **fantasy-relevant availability risk**: the chance a player's
usable games are reduced. It is built from injury designations, practice
reports, historical availability, age, and workload.

It is **not** a medical model. It does not diagnose, does not predict a specific
injury, and cannot distinguish a sprained ankle from a torn one — because the
public injury report it reads does not distinguish them either.

Every assessment the API returns carries its own `limitations` array, and the UI
renders it. That is not decoration; it is the honest framing of what the number
means.

### Language

The product says "elevated availability risk", "historical risk signal",
"model-estimated availability". It never says a player will get injured. This is
a hard rule, not a stylistic preference.

---

## Two modes

### Heuristic (active)

`heuristic-v1`. Transparent, additive, every weight a named constant with stated
reasoning. It works from day one, needs no training data, and is always the
fallback.

### Learned (not enabled)

Scaffolded in `src/fhe/ml/`, deliberately **not** in production. Promoting a
learned model requires all of:

1. A defensible target — probability of injury-related unavailability over an
   explicitly stated horizon.
2. A leakage audit. Features must use only what was knowable at prediction time.
   This is why `observed_at` exists on every time-sensitive table.
3. Time-based train/validation/test splits. Never random splits; a random split
   across seasons leaks the future into the past.
4. A baseline to beat — the heuristic, and "predict the base rate".
5. Calibration, with a reliability plot. An uncalibrated 0.7 is not a
   probability, and this product treats it as one.
6. A meaningful out-of-sample improvement. A model that trains is not a model
   that works.
7. A model card recording version, training window, features, metrics, and
   limitations.

A model that trains but is not calibrated and validated is worse than the
heuristic, because it *looks* more authoritative while being less honest.

---

## The heuristic's inputs

Every contribution is itemised, signed, explained in words, and sums to the raw
score.

### Current designation — the strongest single signal

An official game-status designation is a direct statement about near-term
availability.

| Designation | Points |
| --- | --- |
| IR | 78 |
| PUP | 62 |
| NFI | 55 |
| Out | 48 |
| Suspended | 40 |
| Doubtful | 32 |
| Not active | 25 |
| Did not report | 20 |
| COVID | 15 |
| Questionable | 14 |
| Active / none | 0 |

Spaced so a season-ending designation cannot be offset by any combination of
soft signals.

**"Active" means "no designation on file", not "verified healthy".** The
distinction matters, and the confidence measure carries it.

### Practice trajectory

Modelled separately from game status, because they answer different questions.
"Questionable after three full practices" and "Questionable after three DNPs"
are the same designation and very different signals.

| Trajectory | Points |
| --- | --- |
| Worsening | +12 |
| Stable | 0 |
| Improving | −8 |

The asymmetry is deliberate: recovering practice participation is weaker
evidence of availability than declining participation is of absence. A trailing
run of DNPs adds 6 points each, up to three.

Reports of `UNKNOWN` are dropped rather than treated as a middle value — an
unreported day is missing data, not partial participation.

### Injury history

Three seasons, recency-weighted 1.0 / 0.6 / 0.3.

- **Burden**: 2.2 points per weighted event, capped at 18.
- **Recurrence**: 5 points per repeat in the same region, 8 for soft tissue
  (hamstring, quadriceps, calf, hip/groin, achilles), capped at 16. Repeated
  injuries to the same area are the most durable predictor in the public
  literature, and soft-tissue recurrence is stronger still.
- **Games missed**: 1.1 points each, capped at 14. This is the outcome the model
  actually cares about.

Rest days and personal matters are excluded from burden entirely. Counting
"Not injury related — resting player" as an injury would penalise exactly the
players whose teams manage their load well.

### Age

Position-specific, because running backs decline earliest and steepest — one of
the most consistently observed effects in football analytics.

| Position | Threshold | Points per year beyond |
| --- | --- | --- |
| RB | 26 | 3.4 |
| WR | 29 | 2.2 |
| TE | 30 | 2.0 |
| QB | 35 | 1.6 |

Capped at 15. Rookies add 2.5 for having no professional durability record.

### Workload

High touch volume raises exposure: above 18 touches per game for a back, 9 for a
receiver, 7 for a tight end, adds 4 points.

Conversely a full season of heavy usage is *evidence of durability*, and
16+ games played subtracts 5. A model that only penalises usage would rank the
most reliable workhorses as the riskiest players.

---

## Confidence, and why it is separate

Confidence measures **data completeness**, not correctness. It sums the
weight of the inputs actually present: designation 0.25, injury history 0.25,
workload 0.20, age 0.15, practice 0.15.

This exists because of a specific failure mode. A player with no injury history
and no workload record scores 0 risk — and that must not read as "safe". It
means *unmeasured*. Below 0.5 confidence the assessment adds an explicit caveat,
and the war room's "safest pick" slot requires both low risk **and** sufficient
confidence, so an unknown player can never be presented as the safe option.

---

## Availability estimate

Risk maps to expected share of games available:

```
availability = clamp(0.94 − 0.89 × (risk / 100), 0.05, 1.0)
```

Anchored at 0.94 rather than 1.0 for a clean profile, because even healthy NFL
players miss games. Presenting 100% availability for anyone would be a claim the
data cannot support.

---

## Injury text normalisation

Providers publish free text. The taxonomy maps it to a controlled set of body
regions while **always preserving the original string** — a mapping bug must be
fixable by re-running normalisation over stored rows.

### Traps the real data contains

- **Laterality**: `"right Shoulder"`, `"Left Knee"`, `"Right Wrist"`.
- **Case inconsistency**: `"right Shoulder"` and `"Right Shoulder"`.
- **Plurals**: `"Ribs"`/`"Rib"`, `"Ankles"`/`"Ankle"`, `"calves"`.
- **Truncation**: `"Not injury related - resting p"`.
- **Multi-part**: `"back, ankle, knee"`.
- **Compound**: `"Knee - ACL + MCL"`.
- **Non-injury reasons** that must not become injuries: rest, coach's decision,
  personal matter, travel, suspension, jury duty.
- **Whitespace padding**: nflverse ships literal `"\n    "` practice rows that
  must normalise to UNKNOWN, not be read as participation.

### Word boundaries, not substrings

Matching uses word-boundary regex throughout. Substring matching classified
`"chest"` as a rest day, because "chest" contains "rest". There is a regression
test named after it.

### Measured coverage

**99.97%** of 62,915 descriptor observations across seven nflverse seasons
(2019–2025) map to a controlled region. The floor is asserted by a test, so a
refactor cannot silently degrade it.

The remainder — `"Cramps"`, `"Other"`, `"--"`, `"Medical"` — are genuinely not
body regions, and are recorded as `OTHER_UNKNOWN` rather than forced into a
bucket that would imply information the source never provided.

---

## Limitations, stated plainly

1. **Not validated against outcomes.** The weights are reasoned, not fitted.
   That is exactly why it is labelled heuristic.
2. **Public injury reports omit severity and prognosis.** A "Knee" report covers
   both a bruise and a torn ACL.
3. **Teams strategically under-report.** The input is a negotiated document, not
   a medical record.
4. **Absence of a report is not evidence of health.** Handled through confidence,
   but it remains a real limit on what can be known.
5. **Age curves are population averages.** They say nothing about an individual.
6. **Workload is exposure and durability at once**, and the model treats both
   crudely.
7. **No positional injury base rates.** Different positions sustain different
   injuries at different rates; not modelled.
8. **No in-season update from news.** Health reflects the last ingestion, and
   the UI shows that timestamp.

---

## What would improve it, in order

1. Ingest weekly stats and snap counts so workload is measured rather than
   approximated.
2. Build a leakage-audited training set keyed on `observed_at`, and establish a
   baseline before attempting any model.
3. Calibrate, and publish the reliability plot in the model card.
4. Add positional injury base rates.
5. Only then consider a gradient-boosted model — and only promote it if it beats
   the heuristic out of sample on a time-based split.
