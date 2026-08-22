# Model card — availability risk

Per the build directive's requirement that any model ships with a card. This one
documents a **heuristic**, and states plainly that no learned model is in
production.

---

## Model details

| | |
| --- | --- |
| **Name** | `heuristic-v1` |
| **Type** | Transparent additive scoring. **Not** a learned model. |
| **Version** | 1.0 · 2026-08-22 |
| **Input** | Injury designation, practice trajectory, injury history, age, workload |
| **Output** | `risk_score` 0–100, `availability_estimate` 0–1, `confidence` 0–1, and the signed components that sum to the raw score |
| **Source** | [`src/fhe/core/health/heuristic.py`](../src/fhe/core/health/heuristic.py) |

## Intended use

Estimating **fantasy-relevant availability risk** to inform a draft decision:
how likely a player's usable games are to be reduced.

### Out of scope, explicitly

- Any medical or clinical use
- Predicting a specific injury, its type, or its severity
- Individual player health assessment outside fantasy context
- Insurance, wagering, or contract valuation

## Factors

| Factor | Weight range | Rationale |
| --- | --- | --- |
| Current designation | 0 to +78 | The strongest single signal; a direct statement about near-term availability |
| Practice trajectory | −8 to +12 | Asymmetric: declining participation is stronger evidence than recovering |
| Injury burden | 0 to +18 | Recency-weighted over three seasons |
| Same-region recurrence | 0 to +16 | The most durable predictor in public literature; soft tissue weighted higher |
| Games missed | 0 to +14 | The outcome actually being estimated |
| Age | 0 to +15 | Position-specific; running backs decline earliest and steepest |
| Workload | −5 to +4 | Exposure raises risk; a full season demonstrates durability |

## Metrics

**None. This model has not been evaluated against outcomes.**

That is not an oversight, it is the reason it is called a heuristic. Its weights
are reasoned from public analytics literature and domain knowledge, not fitted.

What *is* verified:

- Components always sum to the raw score (test-asserted)
- Score is bounded 0–100, with the pre-clamp raw value exposed
- Designations are strictly ordered by severity
- Missing data lowers confidence rather than producing risk
- Rest days and personal matters never accrue injury burden
- Injury taxonomy maps 99.97% of 62,915 real observations

## Evaluation data

None. See above.

## Training data

None. There is no training.

## Ethical considerations

**Player privacy.** Uses only publicly published injury reports. No medical
records, no private data.

**Harm from misuse.** A risk score could be misread as a medical judgement about
a real person. Mitigations: every assessment carries a `limitations` array that
the UI renders; the product's language is "availability risk", never "will get
injured"; and the score is explicitly framed as fantasy availability.

**Fairness.** Age curves are population averages by position and say nothing
about an individual. A player flagged by the age term is not "declining"; their
positional cohort historically is.

## Caveats and recommendations

1. **Public injury reports omit severity and prognosis.** A "Knee" report covers
   both a bruise and a torn ACL.
2. **Teams strategically under-report.** The input is a negotiated document.
3. **Absence of a report is not evidence of health.** Carried by confidence, but
   a real epistemic limit.
4. **Not validated.** Treat it as a structured way to organise known signals,
   not as a calibrated probability.

## Path to a learned model

`availability_predictions` exists in the schema, and `observed_at` is recorded
on every time-sensitive row specifically to make point-in-time reconstruction
possible. Before anything is written to that table:

1. Define the target — probability of injury-related unavailability over an
   explicit horizon
2. Build the dataset keyed on `observed_at`, and audit it for leakage
3. Time-based train/validation/test splits, never random
4. Establish baselines: the heuristic, and predicting the base rate
5. Evaluate ROC-AUC, PR-AUC, Brier score, and calibration with a reliability plot
6. Promote only on a meaningful out-of-sample improvement
7. Update this card with real metrics

Until then, `heuristic-v1` is what runs, and this card says so.
