# Model card — availability risk

Two models are documented here: the **heuristic that ships**, and the **learned
model that does not**. The distinction, and the reason for it, is the point of
this document.

Last evaluated: **2026-08-23**.

---

## Model 1 — `heuristic-v1` (in production)

| | |
| --- | --- |
| **Type** | Transparent additive scoring. Not learned. |
| **Version** | 1.0 |
| **Input** | Injury designation, practice trajectory, injury history, age, workload |
| **Output** | `risk_score` 0–100, `availability_estimate` 0–1, `confidence` 0–1, plus the signed components that sum to the raw score |
| **Source** | [`src/fhe/core/health/heuristic.py`](../src/fhe/core/health/heuristic.py) |

Its factors and weights are documented in
[`docs/INJURY_MODEL.md`](INJURY_MODEL.md). It has **no evaluation metrics**,
because it has never been fitted to or validated against outcomes. Its weights
are reasoned from public analytics literature and domain knowledge. That is
exactly why it is called a heuristic, and why it is presented with a confidence
measure rather than as a probability.

---

## Model 2 — learned availability model (**not** in production)

### Intended target

> The probability that an official injury report rules a player out of a game in
> the next **4 weeks**, given only what was known at the time of prediction.

This is deliberately narrower than "the player will be unavailable". It is *a
reported ruling-out*, which is what public data actually records. A player rested
for load management, or inactive by a coach's decision, is not a positive.

### Dataset

| | |
| --- | --- |
| Seasons | 2016–2025 |
| Rows | 58,202 player-weeks |
| Positives | 6,361 (**10.93%** base rate) |
| Players | 1,686 |
| Horizon | 4 weeks |
| Builder | [`src/fhe/ml/dataset.py`](../src/fhe/ml/dataset.py) |

Every feature is computed strictly from weeks **before** the prediction week,
plus that week's injury report — which was known at the time. Season aggregates
are never used, because a season total contains the future.

### Leakage audit

Run by `fhe ml evaluate`; implementation in
[`src/fhe/ml/leakage.py`](../src/fhe/ml/leakage.py). All seven checks pass:

| Check | Result |
| --- | --- |
| No identifiers or time keys in the feature set | pass |
| Label balance within a learnable range | pass — 10.93% |
| No duplicated player-weeks | pass — 0 |
| No non-finite values | pass |
| No single feature separates the classes suspiciously well | pass |
| Cohort is not survivorship-biased | pass — only 7% of the 2016 cohort is still present in 2025 |
| **Features are point-in-time** | pass — **31,397 rows before week 10 are identical with and without future data** |

The last check is the strongest available. The dataset is rebuilt with every
week after a cutoff removed, and the earlier rows must come out identical. If any
feature reached forward, hiding the future would change it. That is a structural
proof rather than a correlation heuristic.

### Evaluation

Trained on 2016–2023, tested on the **held-out 2024 and 2025 seasons**. Whole
seasons, never a date cut: a mid-season split leaves the same player's adjacent
weeks on both sides.

**Baselines** — a model must beat these or it has learned nothing:

| Baseline | ROC-AUC | PR-AUC | Brier |
| --- | --- | --- | --- |
| Predict the base rate | 0.500 | 0.120 | 0.1057 |
| **Read the injury report alone** | **0.612** | 0.217 | **0.0981** |

**Candidates:**

| Model | ROC-AUC | PR-AUC | Brier |
| --- | --- | --- | --- |
| Logistic, `class_weight="balanced"`, uncalibrated | 0.647 | 0.267 | **0.2212** |
| **Logistic regression** | 0.646 | 0.268 | **0.0978** |
| Gradient boosting, isotonic-calibrated | 0.589 | 0.198 | 0.1020 |

**Calibration** of the selected model, on held-out data:

| Predicted | n | Mean predicted | Observed |
| --- | --- | --- | --- |
| 0.0–0.1 | 8,098 | 0.073 | 0.092 |
| 0.1–0.2 | 3,131 | 0.121 | 0.110 |
| 0.2–0.3 | 166 | 0.241 | 0.313 |
| 0.3–0.4 | 143 | 0.350 | 0.343 |
| 0.4–0.5 | 175 | 0.454 | 0.434 |
| 0.5–0.6 | 228 | 0.548 | 0.509 |
| 0.6–0.7 | 98 | 0.639 | 0.551 |

Worst decile gap: **0.088**.

**Strongest coefficients:** current designation (+0.42), week (−0.23), age
(−0.20), practice DNP (+0.13), weeks since last report (−0.13).

### The result worth reading carefully

The `balanced` variant ranks best on ROC-AUC — and its Brier score, **0.2212**,
is more than twice as bad as simply predicting the base rate. Reweighting the
loss bought ranking and destroyed calibration. Its worst decile predicted 0.92
where the observed rate was 0.53.

This product consumes the output *as a probability*: it multiplies into a draft
score and is displayed as "79% available". A model that ranks marginally better
while emitting numbers that are not probabilities is not an improvement, it is a
more confident-looking mistake.

So the promotion bar requires all three of:

1. beat the best baseline on ROC-AUC by more than noise (> 0.02),
2. be at least as well calibrated as that baseline, by Brier score,
3. have no decile where predicted and observed diverge by more than 0.10.

And model *selection* uses the same rule, because choosing the "best" model on
ROC-AUC alone repeats exactly the mistake the bar exists to catch.

**On those criteria the plain logistic regression passes**: +0.034 ROC-AUC over
reading the injury report, a marginally better Brier score, and a worst decile
gap of 0.088.

### Why it is still not in production

Passing a bar is necessary, not sufficient. Three reasons it has not been
promoted:

1. **The absolute performance is modest.** ROC-AUC 0.646 is weakly predictive.
   The gain over "read the injury report" is real but small, and a user is not
   obviously better served by it than by the designation already on their screen.
2. **The serving path does not exist.** There is no model registry, no versioned
   artefact, no inference monitoring, and no way to detect drift once a model is
   live. Shipping a model without those is shipping something nobody can tell has
   broken.
3. **The target is narrower than the product's claim.** The model predicts a
   *reported ruling-out*; the UI speaks about availability. Closing that gap
   needs game-level participation data, not more modelling.

Reproduce all of the above with:

```bash
./.venv/bin/python -m fhe.cli ml evaluate
```

---

## Ethical considerations

**Player privacy.** Only publicly published injury reports. No medical records.

**Harm from misuse.** A risk score could be read as a medical judgement about a
real person. Every assessment carries a `limitations` array the UI renders; the
product says "availability risk", never "will get injured".

**Fairness.** Age curves are population averages by position and say nothing
about an individual. A player flagged by the age term is not declining; their
positional cohort historically is.

---

## Caveats

1. **Public injury reports omit severity and prognosis.** A "Knee" report covers
   both a bruise and a torn ACL.
2. **Teams strategically under-report.** The input is a negotiated document.
3. **Absence of a report is not evidence of health.** Carried by the confidence
   measure, but a real epistemic limit.
4. **The cohort is "players Sleeper still lists"**, not everyone who ever played.
   Attrition looks normal (7% of the 2016 cohort remains in 2025), but the long
   tail of short careers is under-represented.
5. **`heuristic-v1` has never been validated.** Treat it as a structured way to
   organise known signals, not as a calibrated probability.

## What would change the decision

- Game-level participation data, making the target true unavailability rather
  than a reported ruling-out.
- A model registry with versioned artefacts, and drift monitoring on live
  predictions.
- A materially larger ranking gain — ROC-AUC in the 0.70s over the designation
  baseline, sustained across more than two held-out seasons.
