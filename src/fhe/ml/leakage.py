"""Leakage audit.

A model that has seen the future scores beautifully and is worthless. These
checks run against the built frame rather than trusting the builder's docstring,
because "I was careful" is not evidence.

The strongest check here is :func:`check_features_are_point_in_time`, which
rebuilds the dataset with later weeks withheld and asserts the earlier rows come
out **byte-identical**. If any feature secretly depended on the future, hiding
the future would change it. That is a structural proof rather than a correlation
heuristic.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from fhe.ml.dataset import FEATURE_COLUMNS, LABEL_COLUMN
from fhe.observability import get_logger

log = get_logger(__name__)

# A single feature separating the classes this well is almost always the label
# in disguise. Set below 1.0 because a legitimately strong signal exists here:
# a player already designated OUT is genuinely likely to stay out.
SUSPICIOUS_SINGLE_FEATURE_AUC: Final = 0.95

# Below this the label is too rare for any metric to mean anything.
MIN_POSITIVE_RATE: Final = 0.005
MAX_POSITIVE_RATE: Final = 0.60

# Share of the earliest cohort still present in the latest season. Real attrition
# over a decade is heavy; an overlap near 1.0 means the set was assembled from a
# list of currently-active players.
MAX_SURVIVOR_OVERLAP: Final = 0.60


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """One audit result."""

    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        """Render for the CLI."""
        return f"[{'ok  ' if self.passed else 'FAIL'}] {self.name}: {self.detail}"


def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Area under the ROC curve, by rank statistic.

    Implemented directly rather than pulled from scikit-learn so the audit has
    no dependency on the library whose output it is auditing, and so ties are
    handled explicitly (they get half credit, which is what the metric means).
    """
    paired = sorted(zip(scores, labels, strict=True), key=lambda pair: pair[0])
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return 0.5

    # Average ranks for ties.
    ranks: list[float] = [0.0] * len(paired)
    index = 0
    while index < len(paired):
        end = index
        while end + 1 < len(paired) and paired[end + 1][0] == paired[index][0]:
            end += 1
        average = (index + end) / 2 + 1
        for position in range(index, end + 1):
            ranks[position] = average
        index = end + 1

    positive_rank_sum = sum(
        rank for rank, (_, label) in zip(ranks, paired, strict=True) if label == 1
    )
    return (positive_rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def check_label_balance(rows: Sequence[dict[str, Any]]) -> AuditFinding:
    """The label must be rare enough to be interesting and common enough to learn."""
    if not rows:
        return AuditFinding("label_balance", False, "no rows")
    rate = sum(row[LABEL_COLUMN] for row in rows) / len(rows)
    ok = MIN_POSITIVE_RATE <= rate <= MAX_POSITIVE_RATE
    return AuditFinding(
        "label_balance",
        ok,
        f"positive rate {rate:.2%}"
        + ("" if ok else f", outside {MIN_POSITIVE_RATE:.1%}-{MAX_POSITIVE_RATE:.0%}"),
    )


def check_no_duplicate_observations(rows: Sequence[dict[str, Any]]) -> AuditFinding:
    """One row per player-season-week.

    A duplicate leaks across a train/test split: the same observation lands on
    both sides and the model appears to generalise when it has memorised.
    """
    keys = {(row["player_uuid"], row["season"], row["week"]) for row in rows}
    duplicates = len(rows) - len(keys)
    return AuditFinding(
        "no_duplicate_observations",
        duplicates == 0,
        f"{duplicates} duplicated player-weeks",
    )


def check_no_identifiers_in_features() -> AuditFinding:
    """Identifiers and time keys must not be features.

    ``season`` as a feature lets a model learn "2020 was unusual" and then fail
    on any year it has not seen. ``player_uuid`` lets it memorise individuals.
    """
    forbidden = {"player_uuid", "season", LABEL_COLUMN}
    leaked = forbidden & set(FEATURE_COLUMNS)
    return AuditFinding(
        "no_identifiers_in_features",
        not leaked,
        f"leaked columns: {sorted(leaked)}" if leaked else "feature set is clean",
    )


def check_no_single_feature_is_the_label(
    rows: Sequence[dict[str, Any]],
) -> AuditFinding:
    """No individual feature should separate the classes almost perfectly."""
    labels = [row[LABEL_COLUMN] for row in rows]
    offenders: list[tuple[str, float]] = []

    for column in FEATURE_COLUMNS:
        values = [float(row[column]) if row[column] is not None else 0.0 for row in rows]
        if len(set(values)) < 2:
            continue
        auc = roc_auc(values, labels)
        # Direction does not matter: a perfectly inverted feature leaks equally.
        separation = max(auc, 1 - auc)
        if separation >= SUSPICIOUS_SINGLE_FEATURE_AUC:
            offenders.append((column, separation))

    return AuditFinding(
        "no_single_feature_is_the_label",
        not offenders,
        (
            "no feature separates the classes suspiciously well"
            if not offenders
            else f"suspicious: {[(c, round(a, 3)) for c, a in offenders]}"
        ),
    )


def check_no_non_finite_values(rows: Sequence[dict[str, Any]]) -> AuditFinding:
    """Infinities and NaNs propagate silently through most estimators."""
    bad: list[str] = []
    for column in FEATURE_COLUMNS:
        for row in rows:
            value = row[column]
            if value is None:
                continue
            if isinstance(value, float) and not math.isfinite(value):
                bad.append(column)
                break
    return AuditFinding(
        "no_non_finite_values",
        not bad,
        f"non-finite values in {bad}" if bad else "all values finite",
    )


def check_features_are_point_in_time(
    full_rows: Sequence[dict[str, Any]],
    truncated_rows: Sequence[dict[str, Any]],
    *,
    cutoff_week: int,
) -> AuditFinding:
    """Hiding the future must not change the past.

    The strongest check available. The dataset is rebuilt with every week after
    ``cutoff_week`` removed; every row from an earlier week must be identical in
    both builds. If a feature reached forward, the value would move.

    Labels are excluded from the comparison, since the label legitimately looks
    ahead - that is what makes it a label.
    """

    def index(rows: Sequence[dict[str, Any]]) -> dict[tuple[str, int, int], dict[str, Any]]:
        return {
            (row["player_uuid"], row["season"], row["week"]): {
                column: row[column] for column in FEATURE_COLUMNS
            }
            for row in rows
            if row["week"] < cutoff_week
        }

    full = index(full_rows)
    truncated = index(truncated_rows)
    shared = set(full) & set(truncated)

    if not shared:
        return AuditFinding("features_are_point_in_time", False, "no overlapping rows to compare")

    mismatches = [key for key in shared if full[key] != truncated[key]]
    return AuditFinding(
        "features_are_point_in_time",
        not mismatches,
        (
            f"{len(shared)} rows before week {cutoff_week} identical with and without future data"
            if not mismatches
            else f"{len(mismatches)} rows changed when future weeks were hidden"
        ),
    )


def check_cohort_is_not_survivorship_biased(
    rows: Sequence[dict[str, Any]],
) -> AuditFinding:
    """Historical seasons must contain players who later left the league.

    The player table is built from a *current* provider payload. If that payload
    only listed active players, every historical season would consist solely of
    careers that survived — and for a model predicting availability, silently
    excluding the players whose careers ended is close to the worst possible
    selection.

    Detected by attrition: the earliest season's cohort should overlap only
    modestly with the most recent one.
    """
    seasons = sorted({row["season"] for row in rows})
    if len(seasons) < 3:
        return AuditFinding(
            "cohort_is_not_survivorship_biased",
            True,
            "too few seasons to assess attrition",
        )

    def cohort(season: int) -> set[str]:
        return {row["player_uuid"] for row in rows if row["season"] == season}

    earliest, latest = cohort(seasons[0]), cohort(seasons[-1])
    if not earliest:
        return AuditFinding("cohort_is_not_survivorship_biased", False, "earliest season is empty")

    overlap = len(earliest & latest) / len(earliest)
    ok = overlap <= MAX_SURVIVOR_OVERLAP
    return AuditFinding(
        "cohort_is_not_survivorship_biased",
        ok,
        f"{overlap:.0%} of the {seasons[0]} cohort is still present in "
        f"{seasons[-1]}" + ("" if ok else "; the set looks restricted to surviving careers"),
    )


def audit(
    rows: Sequence[dict[str, Any]],
    *,
    truncated_rows: Sequence[dict[str, Any]] | None = None,
    cutoff_week: int = 10,
) -> tuple[AuditFinding, ...]:
    """Run every audit check.

    Args:
        rows: The full training frame.
        truncated_rows: The same frame rebuilt with later weeks withheld. When
            omitted the point-in-time check is skipped and reported as such,
            rather than silently passing.
        cutoff_week: The week the truncated build stops at.
    """
    findings = [
        check_no_identifiers_in_features(),
        check_label_balance(rows),
        check_no_duplicate_observations(rows),
        check_no_non_finite_values(rows),
        check_no_single_feature_is_the_label(rows),
        check_cohort_is_not_survivorship_biased(rows),
    ]
    if truncated_rows is not None:
        findings.append(
            check_features_are_point_in_time(rows, truncated_rows, cutoff_week=cutoff_week)
        )
    else:
        findings.append(
            AuditFinding(
                "features_are_point_in_time",
                False,
                "skipped: no truncated rebuild supplied",
            )
        )

    failed = [f for f in findings if not f.passed]
    log.info("leakage_audit_complete", checks=len(findings), failed=len(failed))
    for finding in failed:
        log.error("leakage_audit_failed", check=finding.name, detail=finding.detail)
    return tuple(findings)
