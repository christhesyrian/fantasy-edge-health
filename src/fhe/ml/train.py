"""Train and evaluate an availability model.

Nothing here promotes anything. It produces evidence, and the promotion decision
is a human one taken against the bar in ``docs/MODEL_CARD.md``.

Evaluation discipline
---------------------
* **Time-based splits only.** Random splits put week 3 of a season in train and
  week 2 in test, which is a time machine. Seasons are held out whole.
* **Baselines first.** A model must beat predicting the base rate *and* beat the
  single strongest feature. Beating neither means the fit learned nothing.
* **Calibration is a first-class result, not a footnote.** The product consumes
  a probability, so a well-ranked but badly-scaled model is unusable regardless
  of its AUC.
* **The heuristic is the incumbent.** A learned model earns its place by beating
  what already ships, not by existing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from fhe.ml.dataset import FEATURE_COLUMNS, LABEL_COLUMN
from fhe.observability import get_logger

log = get_logger(__name__)

# Deciles for the reliability curve. Ten buckets over ~6,000 test rows leaves
# enough in each to be meaningful without pretending to more resolution.
CALIBRATION_BINS: Final = 10

# Missing values are imputed to zero *after* an explicit indicator is added, so
# "no snap history" is a fact the model can use rather than a fabricated zero.
MISSING_INDICATOR_SUFFIX: Final = "_is_missing"

RANDOM_STATE: Final = 20260823

# The largest acceptable disagreement between a predicted decile and its
# observed rate. Beyond this the number cannot be shown to a user as a
# probability, whatever its ranking quality.
MAX_ACCEPTABLE_CALIBRATION_GAP: Final = 0.10


@dataclass(frozen=True, slots=True)
class Metrics:
    """Out-of-sample performance for one model."""

    name: str
    roc_auc: float
    pr_auc: float
    brier: float
    positive_rate: float
    n: int

    def __str__(self) -> str:
        """Render one row of the comparison table."""
        return (
            f"  {self.name:<28} ROC-AUC {self.roc_auc:.3f}  "
            f"PR-AUC {self.pr_auc:.3f}  Brier {self.brier:.4f}"
        )


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """One bucket of the reliability curve."""

    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float

    @property
    def gap(self) -> float:
        """How far predicted sits from observed. The thing calibration fixes."""
        return abs(self.mean_predicted - self.observed_rate)


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Everything needed to decide whether a model may be promoted."""

    train_seasons: tuple[int, ...]
    test_seasons: tuple[int, ...]
    baselines: tuple[Metrics, ...]
    candidates: tuple[Metrics, ...]
    calibration: tuple[CalibrationBin, ...]
    feature_importance: tuple[tuple[str, float], ...] = field(default=())

    @property
    def best_candidate(self) -> Metrics | None:
        """The best *usable* model, not simply the best-ranking one.

        Selecting purely on ROC-AUC repeats the mistake the promotion bar exists
        to catch: it would pick a model that ranks a hair better while emitting
        numbers that are not probabilities. So the choice is made among
        candidates whose Brier score is no worse than the best baseline's, and
        only falls back to raw ranking when none qualify — in which case the bar
        will reject the result anyway, which is the correct outcome.
        """
        if not self.candidates:
            return None
        baseline = self.best_baseline
        if baseline is not None:
            usable = [m for m in self.candidates if m.brier <= baseline.brier]
            if usable:
                return max(usable, key=lambda m: m.roc_auc)
        return max(self.candidates, key=lambda m: m.roc_auc)

    @property
    def best_baseline(self) -> Metrics | None:
        """Highest ROC-AUC among the baselines it must beat."""
        return max(self.baselines, key=lambda m: m.roc_auc) if self.baselines else None

    @property
    def max_calibration_gap(self) -> float:
        """Worst bucket-level disagreement between predicted and observed."""
        return max((b.gap for b in self.calibration if b.count >= 20), default=0.0)

    def promotion_verdict(
        self, *, auc_margin: float = 0.02, max_gap: float = MAX_ACCEPTABLE_CALIBRATION_GAP
    ) -> tuple[bool, tuple[str, ...]]:
        """Whether a model may be promoted, and every reason it may not.

        Ranking alone is not enough. This product consumes the output *as a
        probability* — it multiplies into a draft score and is shown to a user
        as "79% available" — so a model that ranks better while being badly
        scaled is not an improvement, it is a more confident-looking mistake.

        Three conditions, all required:

        1. Beat the best baseline on ROC-AUC by more than noise.
        2. Be at least as well calibrated as that baseline, by Brier score.
        3. Have no decile where predicted and observed diverge wildly.
        """
        best, baseline = self.best_candidate, self.best_baseline
        if best is None or baseline is None:
            return False, ("no candidate or no baseline to compare",)

        reasons: list[str] = []
        if best.roc_auc < baseline.roc_auc + auc_margin:
            reasons.append(
                f"ROC-AUC {best.roc_auc:.3f} does not beat the {baseline.name} "
                f"baseline ({baseline.roc_auc:.3f}) by the {auc_margin:.2f} margin"
            )
        if best.brier > baseline.brier:
            reasons.append(
                f"Brier {best.brier:.4f} is worse than the {baseline.name} "
                f"baseline ({baseline.brier:.4f}); the probabilities are not usable"
            )
        if self.max_calibration_gap > max_gap:
            reasons.append(
                f"worst calibration gap {self.max_calibration_gap:.3f} exceeds {max_gap:.2f}"
            )
        return not reasons, tuple(reasons)

    def beats_baselines(self, *, margin: float = 0.02) -> bool:
        """Whether the promotion bar is met in full."""
        met, _ = self.promotion_verdict(auc_margin=margin)
        return met

    def summary(self) -> str:
        """Render the full evaluation for a human."""
        lines = [
            f"train seasons: {', '.join(map(str, self.train_seasons))}",
            f"test seasons:  {', '.join(map(str, self.test_seasons))}",
            "",
            "baselines:",
            *[str(m) for m in self.baselines],
            "",
            "candidates:",
            *[str(m) for m in self.candidates],
            "",
            "calibration (predicted vs observed):",
        ]
        for entry in self.calibration:
            if entry.count == 0:
                continue
            bar = "#" * int(entry.observed_rate * 40)
            lines.append(
                f"  {entry.lower:.1f}-{entry.upper:.1f}  n={entry.count:>5}  "
                f"pred {entry.mean_predicted:.3f}  obs {entry.observed_rate:.3f}  {bar}"
            )
        if self.feature_importance:
            lines += ["", "strongest features (permutation-free, model coefficients):"]
            lines += [
                f"  {name:<32} {weight:+.3f}" for name, weight in self.feature_importance[:10]
            ]
        best, baseline = self.best_candidate, self.best_baseline
        if best and baseline:
            met, reasons = self.promotion_verdict()
            lines += [
                "",
                f"best candidate ({best.name}) vs best baseline ({baseline.name}): "
                f"{best.roc_auc - baseline.roc_auc:+.3f} ROC-AUC, "
                f"{best.brier - baseline.brier:+.4f} Brier",
                f"worst calibration gap: {self.max_calibration_gap:.3f}",
                f"PROMOTION BAR MET: {met}",
            ]
            lines += [f"  blocked by: {reason}" for reason in reasons]
        return "\n".join(lines)


def to_matrix(rows: Sequence[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build the feature matrix, with explicit missing-value indicators.

    A missing rolling snap count means "this player has not played yet", which
    is informative. Imputing zero without an indicator would tell the model the
    player played and recorded nothing.
    """
    columns: list[str] = []
    optional = [column for column in FEATURE_COLUMNS if any(row[column] is None for row in rows)]

    matrix: list[list[float]] = []
    for row in rows:
        values: list[float] = []
        for column in FEATURE_COLUMNS:
            value = row[column]
            values.append(float(value) if value is not None else 0.0)
        for column in optional:
            values.append(1.0 if row[column] is None else 0.0)
        matrix.append(values)

    columns = list(FEATURE_COLUMNS) + [f"{column}{MISSING_INDICATOR_SUFFIX}" for column in optional]
    labels = np.array([row[LABEL_COLUMN] for row in rows], dtype=int)
    return np.array(matrix, dtype=float), labels, columns


def split_by_season(
    rows: Sequence[dict[str, Any]], *, test_seasons: Sequence[int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Hold out whole seasons.

    Whole seasons rather than a date cut, because a mid-season split leaves the
    same player's adjacent weeks on both sides, which is the same player in two
    places and nearly the same observation.
    """
    held = set(test_seasons)
    train = [row for row in rows if row["season"] not in held]
    test = [row for row in rows if row["season"] in held]
    return train, test


def _metrics(name: str, probabilities: np.ndarray, labels: np.ndarray) -> Metrics:
    """Compute the evaluation metrics for one set of predictions."""
    return Metrics(
        name=name,
        roc_auc=float(roc_auc_score(labels, probabilities)),
        pr_auc=float(average_precision_score(labels, probabilities)),
        brier=float(brier_score_loss(labels, probabilities)),
        positive_rate=float(labels.mean()),
        n=int(labels.size),
    )


def calibration_curve(
    probabilities: np.ndarray, labels: np.ndarray, bins: int = CALIBRATION_BINS
) -> tuple[CalibrationBin, ...]:
    """Bucket predictions and compare each bucket's mean to its observed rate."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    out: list[CalibrationBin] = []
    for index in range(bins):
        lower, upper = edges[index], edges[index + 1]
        mask = (
            (probabilities >= lower) & (probabilities < upper)
            if index < bins - 1
            else (probabilities >= lower) & (probabilities <= upper)
        )
        count = int(mask.sum())
        out.append(
            CalibrationBin(
                lower=float(lower),
                upper=float(upper),
                count=count,
                mean_predicted=float(probabilities[mask].mean()) if count else 0.0,
                observed_rate=float(labels[mask].mean()) if count else 0.0,
            )
        )
    return tuple(out)


def evaluate(rows: Sequence[dict[str, Any]], *, test_seasons: Sequence[int]) -> Evaluation:
    """Fit baselines and candidates, and measure them out of sample."""
    train_rows, test_rows = split_by_season(rows, test_seasons=test_seasons)
    if not train_rows or not test_rows:
        raise ValueError("split produced an empty side; check the requested seasons")

    x_train, y_train, columns = to_matrix(train_rows)
    x_test, y_test, _ = to_matrix(test_rows)

    baselines: list[Metrics] = []

    # Baseline 1: predict the base rate for everyone. Any model that cannot beat
    # this has learned nothing at all.
    prior = DummyClassifier(strategy="prior").fit(x_train, y_train)
    baselines.append(_metrics("base rate", prior.predict_proba(x_test)[:, 1], y_test))

    # Baseline 2: the current designation alone. This is the bar that actually
    # matters — it asks whether the model does more than read the injury report
    # already on the screen.
    #
    # The raw feature is an ordinal severity, not a probability, so it is mapped
    # onto the observed rate for each severity in the *training* data. That
    # keeps Brier meaningful and keeps the mapping out-of-sample, rather than
    # dividing by the maximum and calling the result a probability.
    designation_index = columns.index("carried_designation")
    train_designation = x_train[:, designation_index]
    test_designation = x_test[:, designation_index]
    rates = {
        float(level): float(y_train[train_designation == level].mean())
        for level in np.unique(train_designation)
        if (train_designation == level).sum() > 0
    }
    fallback = float(y_train.mean())
    designation_probabilities = np.array(
        [rates.get(float(value), fallback) for value in test_designation]
    )
    baselines.append(_metrics("designation only", designation_probabilities, y_test))

    candidates: list[Metrics] = []

    # Kept deliberately, and reported, because it demonstrates the trap this
    # evaluation exists to catch: class_weight="balanced" reweights the loss so
    # the model ranks better, and in exchange its outputs stop being
    # probabilities entirely.
    balanced = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    max_iter=2000, class_weight="balanced", random_state=RANDOM_STATE
                ),
            ),
        ]
    ).fit(x_train, y_train)
    balanced_probabilities = balanced.predict_proba(x_test)[:, 1]
    candidates.append(_metrics("logistic (balanced, uncalibrated)", balanced_probabilities, y_test))

    logistic = Pipeline(
        [
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=2000, random_state=RANDOM_STATE)),
        ]
    ).fit(x_train, y_train)
    logistic_probabilities = logistic.predict_proba(x_test)[:, 1]
    candidates.append(_metrics("logistic regression", logistic_probabilities, y_test))

    # Gradient boosting, calibrated with isotonic regression on held-out folds.
    # Uncalibrated boosting produces confident scores that are not probabilities,
    # and this product consumes them as probabilities.
    boosted = CalibratedClassifierCV(
        HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, random_state=RANDOM_STATE),
        method="isotonic",
        cv=3,
    ).fit(x_train, y_train)
    boosted_probabilities = boosted.predict_proba(x_test)[:, 1]
    candidates.append(_metrics("gradient boosting (calibrated)", boosted_probabilities, y_test))

    by_name = {
        "logistic (balanced, uncalibrated)": balanced_probabilities,
        "logistic regression": logistic_probabilities,
        "gradient boosting (calibrated)": boosted_probabilities,
    }
    coefficients = logistic.named_steps["model"].coef_[0]
    importance = tuple(
        sorted(
            zip(columns, (float(c) for c in coefficients), strict=True),
            key=lambda pair: abs(pair[1]),
            reverse=True,
        )
    )

    # Build once without a curve, so best_candidate can apply the usability
    # rule, then attach the curve for whichever model that selects. Plotting the
    # calibration of a model nobody would ship would be misleading.
    provisional = Evaluation(
        train_seasons=tuple(sorted({row["season"] for row in train_rows})),
        test_seasons=tuple(sorted({row["season"] for row in test_rows})),
        baselines=tuple(baselines),
        candidates=tuple(candidates),
        calibration=(),
        feature_importance=importance,
    )
    selected = provisional.best_candidate
    best_probabilities = by_name[selected.name] if selected is not None else logistic_probabilities

    evaluation = Evaluation(
        train_seasons=provisional.train_seasons,
        test_seasons=provisional.test_seasons,
        baselines=provisional.baselines,
        candidates=provisional.candidates,
        calibration=calibration_curve(best_probabilities, y_test),
        feature_importance=importance,
    )

    log.info(
        "model_evaluation_complete",
        train_rows=len(train_rows),
        test_rows=len(test_rows),
        best_auc=evaluation.best_candidate.roc_auc if evaluation.best_candidate else None,
        promotable=evaluation.beats_baselines(),
    )
    return evaluation
