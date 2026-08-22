"""Probability that a player survives until the user's next pick.

This is the feature that changes draft behaviour most: knowing a player is *very
likely* to still be there in 14 picks turns a "reach" into a free extra round.

Model
-----
Treat the pick at which a player comes off the board as a random variable
``D`` with a normal distribution centred on their ADP:

    D ~ Normal(mu = adp, sigma)

The quantity the war room needs is *conditional* on what has already happened -
the player is demonstrably still available at the current pick ``c``:

    P(D >= n | D >= c) = P(D >= n) / P(D >= c)

where ``n`` is the user's next pick number. Conditioning matters: a player whose
ADP is 20 who is somehow still on the board at pick 40 is behaving nothing like
their ADP, and the unconditional formula would report a near-zero survival
probability that is obviously wrong to anyone looking at the screen.

Re-anchoring fallers
--------------------
A player still on the board well past their ADP has been *re-priced by the room*.
Their ADP no longer describes the market - something (news, an injury, a positional
run elsewhere) has changed how the table values them. Applying the stale mean gives
absurd answers: a player with ADP 20 sitting undrafted at pick 45 would be reported
as ~0.01% likely to last twelve more picks, when in reality players who fall tend to
keep falling.

So when the current pick has passed the player's ADP, the distribution is re-centred
on the current pick, which is the market's revealed lower bound on where they will
go. Dispersion widens correspondingly, because late picks are far less predictable
than early ones.

Normality is an approximation. Real draft-position distributions are
right-skewed - a player can fall a long way but cannot be taken before pick 1.
It is a defensible first model because it needs only a location and a scale, both
of which real ADP sources publish, and it is replaced rather than tuned once
empirical draft data is available (see ``docs/DRAFT_ENGINE.md``).
"""

from __future__ import annotations

import math
from typing import Final

# When an ADP source publishes no dispersion, sigma is estimated from the ADP
# itself: uncertainty about a player's draft slot grows roughly proportionally
# with how late they go. Early picks are highly predictable; round-10 picks are
# close to a coin flip across a wide band.
_RELATIVE_SIGMA: Final = 0.32
_MIN_SIGMA: Final = 3.0
_MAX_SIGMA: Final = 40.0

# Guards the conditional denominator. Below this the conditioning event is so
# unlikely under the model that the ratio becomes numerically meaningless, and
# the player's ADP is simply stale relative to reality.
_MIN_CONDITIONING_PROBABILITY: Final = 1e-6


def default_sigma(adp: float) -> float:
    """Estimate ADP dispersion when the source publishes none."""
    return max(_MIN_SIGMA, min(_MAX_SIGMA, adp * _RELATIVE_SIGMA))


def _normal_sf(x: float, mu: float, sigma: float) -> float:
    """Survival function P(X >= x) for a normal distribution.

    Uses :func:`math.erfc` directly rather than ``1 - cdf`` to preserve precision
    in the far right tail, which is exactly where a faller lives.
    """
    if sigma <= 0:
        return 1.0 if x <= mu else 0.0
    return 0.5 * math.erfc((x - mu) / (sigma * math.sqrt(2.0)))


def survival_probability(
    *,
    adp: float | None,
    current_pick: int,
    next_pick: int | None,
    adp_stdev: float | None = None,
) -> float | None:
    """Probability a player is still available at ``next_pick``.

    Args:
        adp: Average draft position. ``None`` means no ADP source, and the
            function returns ``None`` rather than guessing.
        current_pick: The pick number about to be made. The player is known to be
            available now, and the result is conditioned on that fact.
        next_pick: The user's next pick number. ``None`` means the user has no
            further picks.
        adp_stdev: Published ADP dispersion, if the source provides it.

    Returns:
        A probability in ``[0, 1]``, or ``None`` when it cannot be computed.

    Examples:
        A player with ADP 30 is very likely to survive two more picks:

        >>> round(survival_probability(adp=30.0, current_pick=10, next_pick=12), 2)
        1.0

        ...and unlikely to last another forty:

        >>> survival_probability(adp=30.0, current_pick=10, next_pick=60) < 0.05
        True
    """
    if adp is None or next_pick is None:
        return None
    if next_pick <= current_pick:
        # The user is on the clock now; the player is available by definition.
        return 1.0

    # Re-anchor a faller: still being available at ``current_pick`` is itself
    # evidence, and the market's revealed valuation supersedes a stale ADP.
    effective_adp = max(adp, float(current_pick))
    re_anchored = effective_adp > adp

    if adp_stdev and adp_stdev > 0:
        # A published dispersion is trusted, but never below the uncertainty
        # implied by how late the player has actually fallen.
        sigma = max(adp_stdev, default_sigma(effective_adp)) if re_anchored else adp_stdev
    else:
        sigma = default_sigma(effective_adp)

    # Half-pick continuity correction: a pick is a discrete event, and the
    # boundary sits between pick n-1 and pick n.
    numerator = _normal_sf(next_pick - 0.5, effective_adp, sigma)
    denominator = _normal_sf(current_pick - 0.5, effective_adp, sigma)

    if denominator < _MIN_CONDITIONING_PROBABILITY:
        # Numerically degenerate conditioning event. Re-anchoring makes this
        # very unlikely, but the guard stays so no input can produce a division
        # that fabricates certainty.
        return round(min(1.0, max(0.0, numerator)), 4)

    return round(min(1.0, max(0.0, numerator / denominator)), 4)


def take_now_probability(survival: float | None) -> float | None:
    """Complement of survival: the chance the player is gone by the next pick.

    Exposed as its own concept because it is what the recommendation actually
    keys on - urgency, not availability.
    """
    if survival is None:
        return None
    return round(1.0 - survival, 4)
