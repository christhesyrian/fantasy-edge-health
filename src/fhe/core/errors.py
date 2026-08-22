"""Domain-level exception hierarchy.

These are raised by pure domain code and translated to HTTP responses at the
API edge.  Domain code never raises bare ``Exception`` and never swallows one.
"""

from __future__ import annotations


class FheError(Exception):
    """Base class for every Fantasy Health Edge error."""


class DomainError(FheError):
    """A domain invariant was violated by caller-supplied input."""


class LeagueConfigurationError(DomainError):
    """League settings are internally inconsistent or unsupported."""


class DraftStateError(DomainError):
    """An illegal draft state transition was attempted."""


class DuplicatePickError(DraftStateError):
    """A pick that already exists was applied again.

    Raised only by strict callers; the idempotent ingestion path uses
    :meth:`fhe.core.draft.state.DraftState.apply_pick` return value instead.
    """


class UnknownPlayerError(DomainError):
    """A pick referenced a player absent from the known player pool."""
