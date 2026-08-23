"""Model evaluation.

The properties worth testing are not the metric arithmetic, which scikit-learn
owns, but the *judgement*: that splits are temporal, that a badly calibrated
model cannot be selected, and that the promotion bar refuses what it should.
"""

from __future__ import annotations

import numpy as np
import pytest

from fhe.ml.dataset import FEATURE_COLUMNS, LABEL_COLUMN
from fhe.ml.train import (
    MAX_ACCEPTABLE_CALIBRATION_GAP,
    CalibrationBin,
    Evaluation,
    Metrics,
    calibration_curve,
    evaluate,
    split_by_season,
    to_matrix,
)

pytestmark = pytest.mark.unit


def row(season: int, week: int, label: int, **features: float) -> dict[str, object]:
    """One dataset row."""
    entry: dict[str, object] = {
        "player_uuid": f"p{season}{week}",
        "season": season,
        "week": float(week),
        LABEL_COLUMN: label,
    }
    for column in FEATURE_COLUMNS:
        entry.setdefault(column, 0.0)
    entry.update(features)
    entry["week"] = float(week)
    return entry


def metrics(name: str, auc: float, brier: float) -> Metrics:
    """A metrics record with only the fields the verdict reads."""
    return Metrics(name=name, roc_auc=auc, pr_auc=0.2, brier=brier, positive_rate=0.1, n=100)


class TestSplitting:
    def test_holds_out_whole_seasons(self) -> None:
        """A mid-season cut leaves the same player's adjacent weeks on both
        sides, which is nearly the same observation twice."""
        rows = [row(season, week, 0) for season in (2020, 2021) for week in range(1, 5)]
        train, test = split_by_season(rows, test_seasons=[2021])

        assert {r["season"] for r in train} == {2020}
        assert {r["season"] for r in test} == {2021}

    def test_an_empty_side_is_refused(self) -> None:
        rows = [row(2020, week, 0) for week in range(1, 5)]
        with pytest.raises(ValueError, match="empty side"):
            evaluate(rows, test_seasons=[2099])


class TestMatrix:
    def test_missing_values_get_an_explicit_indicator(self) -> None:
        """Imputing zero without an indicator tells the model the player played
        and recorded nothing, which is a different claim from "did not play"."""
        rows = [
            row(2020, 1, 0, rolling_snaps_per_game=40.0),
            {**row(2020, 2, 0), "rolling_snaps_per_game": None},
        ]
        matrix, labels, columns = to_matrix(rows)

        assert "rolling_snaps_per_game_is_missing" in columns
        indicator = columns.index("rolling_snaps_per_game_is_missing")
        assert matrix[0][indicator] == 0.0
        assert matrix[1][indicator] == 1.0
        assert labels.tolist() == [0, 0]

    def test_no_indicator_when_nothing_is_missing(self) -> None:
        rows = [row(2020, week, 0, rolling_snaps_per_game=10.0) for week in (1, 2)]
        _, _, columns = to_matrix(rows)
        assert columns == list(FEATURE_COLUMNS)


class TestCalibrationCurve:
    def test_buckets_predictions_against_observed_rates(self) -> None:
        probabilities = np.array([0.05] * 100 + [0.95] * 100)
        labels = np.array([0] * 100 + [1] * 100)
        bins = calibration_curve(probabilities, labels, bins=10)

        low = next(b for b in bins if b.lower == 0.0)
        high = next(b for b in bins if b.upper == 1.0)
        assert low.observed_rate == pytest.approx(0.0)
        assert high.observed_rate == pytest.approx(1.0)
        assert low.gap < 0.1

    def test_gap_measures_disagreement(self) -> None:
        entry = CalibrationBin(0.4, 0.5, 100, mean_predicted=0.45, observed_rate=0.10)
        assert entry.gap == pytest.approx(0.35)


class TestPromotionBar:
    def build(self, candidates: list[Metrics], gap: float) -> Evaluation:
        """An evaluation with a controlled calibration gap."""
        return Evaluation(
            train_seasons=(2020,),
            test_seasons=(2021,),
            baselines=(metrics("designation only", 0.61, 0.098),),
            candidates=tuple(candidates),
            calibration=(
                CalibrationBin(0.4, 0.5, 100, mean_predicted=0.45, observed_rate=0.45 - gap),
            ),
        )

    def test_a_well_calibrated_improvement_is_promotable(self) -> None:
        evaluation = self.build([metrics("logistic", 0.65, 0.097)], gap=0.05)
        met, reasons = evaluation.promotion_verdict()
        assert met
        assert reasons == ()

    def test_a_marginal_ranking_gain_is_refused(self) -> None:
        evaluation = self.build([metrics("logistic", 0.615, 0.097)], gap=0.02)
        met, reasons = evaluation.promotion_verdict()
        assert not met
        assert any("margin" in reason for reason in reasons)

    def test_a_badly_calibrated_model_is_refused_however_well_it_ranks(self) -> None:
        """The product shows the output as a probability, so ranking alone is
        not an improvement — it is a more confident-looking mistake."""
        evaluation = self.build([metrics("balanced", 0.72, 0.221)], gap=0.47)
        met, reasons = evaluation.promotion_verdict()

        assert not met
        assert any("Brier" in reason for reason in reasons)
        assert any("calibration gap" in reason for reason in reasons)

    def test_best_candidate_prefers_usable_over_best_ranking(self) -> None:
        """Selecting on ROC-AUC alone repeats the mistake the bar exists to
        catch."""
        evaluation = self.build(
            [
                metrics("balanced (uncalibrated)", 0.72, 0.221),
                metrics("logistic", 0.70, 0.096),
            ],
            gap=0.05,
        )
        best = evaluation.best_candidate
        assert best is not None
        assert best.name == "logistic"

    def test_falls_back_to_ranking_when_nothing_is_usable(self) -> None:
        evaluation = self.build([metrics("balanced", 0.72, 0.30)], gap=0.4)
        best = evaluation.best_candidate
        assert best is not None
        assert best.name == "balanced"
        assert not evaluation.promotion_verdict()[0]

    def test_the_gap_threshold_is_meaningfully_tight(self) -> None:
        assert MAX_ACCEPTABLE_CALIBRATION_GAP <= 0.15


class TestEndToEnd:
    def test_evaluation_runs_and_reports(self) -> None:
        """A learnable signal must produce a model that beats the base rate."""
        rng = np.random.default_rng(7)
        rows = []
        for season in (2020, 2021, 2022):
            for index in range(700):
                severity = float(rng.integers(0, 6))
                # Label correlates with severity, plus noise.
                label = int(rng.random() < 0.03 + severity * 0.13)
                rows.append(
                    row(
                        season,
                        index % 17 + 1,
                        label,
                        carried_designation=severity,
                        age=float(rng.integers(22, 34)),
                    )
                )
                rows[-1]["player_uuid"] = f"p-{season}-{index}"

        evaluation = evaluate(rows, test_seasons=[2022])

        assert evaluation.train_seasons == (2020, 2021)
        assert evaluation.test_seasons == (2022,)
        assert len(evaluation.baselines) == 2
        assert len(evaluation.candidates) == 3

        best = evaluation.best_candidate
        assert best is not None
        assert best.roc_auc > 0.5
        assert "PROMOTION BAR MET" in evaluation.summary()
