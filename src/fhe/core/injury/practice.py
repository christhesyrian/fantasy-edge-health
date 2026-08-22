"""Practice participation normalisation and trajectory.

Practice status is modelled independently from the game designation because the
two carry different information: a "Questionable" tag with three consecutive
full practices is a very different signal from "Questionable" after three DNPs.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from fhe.core.types import PracticeStatus, PracticeTrajectory

_WHITESPACE: Final = re.compile(r"\s+")

# Matched against the whitespace-collapsed, lower-cased string.
# The nflverse feed contains literal "\n    " padding rows, which must normalise
# to UNKNOWN rather than being mistaken for a report.
_PRACTICE_RULES: Final[tuple[tuple[re.Pattern[str], PracticeStatus], ...]] = (
    (re.compile(r"\bdid not participate\b|\bdnp\b|\bdid not practice\b"), PracticeStatus.DNP),
    (re.compile(r"\blimited\b|\blimited participation\b"), PracticeStatus.LIMITED),
    (re.compile(r"\bfull\b|\bfull participation\b"), PracticeStatus.FULL),
)

# A trajectory needs at least this many known reports to be meaningful.
_MIN_REPORTS_FOR_TRAJECTORY: Final = 2


def normalize_practice_status(raw: str | None) -> PracticeStatus:
    """Map a raw practice-report string to a normalised status.

    Examples:
        >>> normalize_practice_status("Did Not Participate In Practice")
        <PracticeStatus.DNP: 'DNP'>
        >>> normalize_practice_status("\\n    ")
        <PracticeStatus.UNKNOWN: 'UNKNOWN'>
    """
    if raw is None:
        return PracticeStatus.UNKNOWN
    text = _WHITESPACE.sub(" ", raw).strip().lower()
    if not text:
        return PracticeStatus.UNKNOWN
    for pattern, status in _PRACTICE_RULES:
        if pattern.search(text):
            return status
    return PracticeStatus.UNKNOWN


def practice_trajectory(
    statuses: Sequence[PracticeStatus],
) -> PracticeTrajectory:
    """Classify the direction of practice participation.

    ``statuses`` must be in chronological order, oldest first. ``UNKNOWN`` entries
    are dropped rather than treated as a middle value, because an unreported day
    is missing data, not partial participation.

    The comparison is between the first and last *known* report:

    * ``DNP -> LIMITED -> FULL`` is improving
    * ``FULL -> LIMITED -> DNP`` is worsening
    * equal endpoints are stable

    This is deliberately one coarse signal rather than a claim of certainty; a
    player can practise fully on Friday and still be inactive on Sunday.
    """
    known = [s for s in statuses if s is not PracticeStatus.UNKNOWN]
    if len(known) < _MIN_REPORTS_FOR_TRAJECTORY:
        return PracticeTrajectory.INSUFFICIENT_DATA

    first = known[0].severity_rank
    last = known[-1].severity_rank
    if last > first:
        return PracticeTrajectory.IMPROVING
    if last < first:
        return PracticeTrajectory.WORSENING
    return PracticeTrajectory.STABLE
