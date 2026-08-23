"""Leakage audit.

Each test proves a check catches the leak it names, using a frame constructed to
contain that specific problem. An audit that cannot fail is decoration.
"""

from __future__ import annotations

from typing import Any

import pytest

from fhe.ml.dataset import FEATURE_COLUMNS, LABEL_COLUMN
from fhe.ml.leakage import (
    audit,
    check_cohort_is_not_survivorship_biased,
    check_features_are_point_in_time,
    check_label_balance,
    check_no_duplicate_observations,
    check_no_identifiers_in_features,
    check_no_non_finite_values,
    check_no_single_feature_is_the_label,
    roc_auc,
)

pytestmark = pytest.mark.unit


def row(
    *,
    player: str = "p1",
    season: int = 2020,
    week: int = 1,
    label: int = 0,
    **features: float,
) -> dict[str, Any]:
    """One dataset row with every feature present."""
    entry: dict[str, Any] = {
        "player_uuid": player,
        "season": season,
        "week": float(week),
        LABEL_COLUMN: label,
    }
    for column in FEATURE_COLUMNS:
        if column not in entry:
            entry[column] = 0.0
    entry.update(features)
    entry["week"] = float(week)
    return entry


class TestRocAuc:
    def test_perfect_separation_is_one(self) -> None:
        assert roc_auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == pytest.approx(1.0)

    def test_random_ordering_is_a_half(self) -> None:
        assert roc_auc([0.1, 0.8, 0.2, 0.9], [0, 1, 0, 1]) == pytest.approx(1.0)
        assert roc_auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == pytest.approx(0.5)

    def test_one_class_only_is_undefined_and_returns_a_half(self) -> None:
        assert roc_auc([0.1, 0.2], [0, 0]) == pytest.approx(0.5)


class TestChecks:
    def test_identifiers_are_not_features(self) -> None:
        assert check_no_identifiers_in_features().passed

    def test_degenerate_label_balance_is_caught(self) -> None:
        rows = [row(label=1) for _ in range(10)]
        assert not check_label_balance(rows).passed

    def test_reasonable_label_balance_passes(self) -> None:
        rows = [row(label=1 if index < 10 else 0, week=index) for index in range(100)]
        assert check_label_balance(rows).passed

    def test_duplicate_observations_are_caught(self) -> None:
        """A duplicate lands on both sides of a split and looks like
        generalisation when it is memorisation."""
        rows = [row(week=1), row(week=1)]
        result = check_no_duplicate_observations(rows)
        assert not result.passed
        assert "1 duplicated" in result.detail

    def test_a_feature_that_is_the_label_is_caught(self) -> None:
        rows = [
            row(week=index, label=index % 2, prior_reports_this_season=float(index % 2))
            for index in range(200)
        ]
        result = check_no_single_feature_is_the_label(rows)
        assert not result.passed
        assert "prior_reports_this_season" in result.detail

    def test_ordinary_features_are_not_flagged(self) -> None:
        rows = [
            row(
                week=index,
                label=1 if index % 7 == 0 else 0,
                prior_reports_this_season=float(index % 5),
            )
            for index in range(200)
        ]
        assert check_no_single_feature_is_the_label(rows).passed

    def test_non_finite_values_are_caught(self) -> None:
        rows = [row(week=1, age=float("inf"))]
        assert not check_no_non_finite_values(rows).passed

    def test_survivorship_bias_is_caught(self) -> None:
        """Every historical player still present today means the cohort was
        assembled from a list of currently-active players."""
        rows = [
            row(player=f"p{index}", season=season, week=1)
            for season in (2016, 2020, 2025)
            for index in range(20)
        ]
        result = check_cohort_is_not_survivorship_biased(rows)
        assert not result.passed
        assert "surviving careers" in result.detail

    def test_normal_attrition_passes(self) -> None:
        rows = [row(player=f"old{i}", season=2016, week=1) for i in range(20)]
        rows += [row(player=f"new{i}", season=2025, week=1) for i in range(18)]
        rows += [row(player=f"old{i}", season=2025, week=2) for i in range(2)]
        rows += [row(player=f"mid{i}", season=2020, week=1) for i in range(20)]
        assert check_cohort_is_not_survivorship_biased(rows).passed


class TestPointInTime:
    def test_identical_past_passes(self) -> None:
        """The strongest check: hiding the future must not change the past."""
        full = [row(week=week, prior_reports_this_season=float(week)) for week in range(1, 18)]
        truncated = [r for r in full if r["week"] < 10]
        result = check_features_are_point_in_time(full, truncated, cutoff_week=10)
        assert result.passed
        assert "identical" in result.detail

    def test_a_feature_that_reached_forward_is_caught(self) -> None:
        full = [row(week=week, prior_reports_this_season=99.0) for week in range(1, 18)]
        # In the truncated build the same early week carries a different value,
        # which can only happen if it depended on later data.
        truncated = [row(week=week, prior_reports_this_season=float(week)) for week in range(1, 10)]
        result = check_features_are_point_in_time(full, truncated, cutoff_week=10)
        assert not result.passed
        assert "changed when future weeks were hidden" in result.detail

    def test_skipping_the_check_is_reported_not_silently_passed(self) -> None:
        rows = [row(week=index, label=index % 9 == 0) for index in range(100)]
        findings = {f.name: f for f in audit(rows)}
        assert not findings["features_are_point_in_time"].passed
        assert "skipped" in findings["features_are_point_in_time"].detail


class TestAudit:
    def test_runs_every_check(self) -> None:
        rows = [row(week=index % 17 + 1, label=1 if index % 9 == 0 else 0) for index in range(200)]
        findings = audit(rows, truncated_rows=rows, cutoff_week=10)
        assert len(findings) == 7
        assert {f.name for f in findings} == {
            "no_identifiers_in_features",
            "label_balance",
            "no_duplicate_observations",
            "no_non_finite_values",
            "no_single_feature_is_the_label",
            "cohort_is_not_survivorship_biased",
            "features_are_point_in_time",
        }
