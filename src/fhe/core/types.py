"""Core domain vocabulary.

Every enum here has an explicit ``UNKNOWN``-style member.  Provider payloads
change without warning, and the system's rule is to *record* an unrecognised
value rather than crash or silently coerce it to something plausible.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class Position(StrEnum):
    """Offensive fantasy positions plus team defense.

    IDP positions are intentionally out of scope for v1; they are recorded as
    ``UNKNOWN`` so a superflex/IDP league does not crash the draft engine.
    """

    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    K = "K"
    DEF = "DEF"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, raw: str | None) -> Position:
        """Map a provider position string onto the domain enum.

        Handles the aliases actually observed in Sleeper and nflverse payloads
        (``DST``/``D/ST`` for team defense, ``PK`` for kicker, ``FB`` charted as
        a running back).  Anything else becomes ``UNKNOWN``.
        """
        if not raw:
            return cls.UNKNOWN
        key = raw.strip().upper().replace("/", "").replace(" ", "")
        aliases = {
            "DST": cls.DEF,
            "DEFENSE": cls.DEF,
            "DEF": cls.DEF,
            "PK": cls.K,
            "FB": cls.RB,
            "HB": cls.RB,
        }
        if key in aliases:
            return aliases[key]
        try:
            return cls(key)
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_flex_eligible(self) -> bool:
        """Whether the position may fill a standard RB/WR/TE FLEX slot."""
        return self in _FLEX_POSITIONS


_FLEX_POSITIONS = frozenset({Position.RB, Position.WR, Position.TE})


@unique
class RosterSlot(StrEnum):
    """A startable (or bench) slot in a league's lineup configuration.

    Names follow Sleeper's ``roster_positions`` vocabulary because that is the
    primary live integration; the manual-league path uses the same tokens.
    """

    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    K = "K"
    DEF = "DEF"
    FLEX = "FLEX"  # RB / WR / TE
    REC_FLEX = "REC_FLEX"  # WR / TE
    WRRB_FLEX = "WRRB_FLEX"  # WR / RB
    SUPER_FLEX = "SUPER_FLEX"  # QB / RB / WR / TE
    BENCH = "BN"
    IR = "IR"
    TAXI = "TAXI"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, raw: str | None) -> RosterSlot:
        """Map a provider roster-position token onto the domain enum."""
        if not raw:
            return cls.UNKNOWN
        key = raw.strip().upper()
        aliases = {
            "BN": cls.BENCH,
            "BENCH": cls.BENCH,
            "DST": cls.DEF,
            "D/ST": cls.DEF,
            "SUPERFLEX": cls.SUPER_FLEX,
            "SFLEX": cls.SUPER_FLEX,
            "WRRB": cls.WRRB_FLEX,
            "FLEX_IDP": cls.UNKNOWN,
            "IDP_FLEX": cls.UNKNOWN,
        }
        if key in aliases:
            return aliases[key]
        try:
            return cls(key)
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_starting_slot(self) -> bool:
        """Whether filling this slot contributes to the weekly starting lineup."""
        return self not in {RosterSlot.BENCH, RosterSlot.IR, RosterSlot.TAXI, RosterSlot.UNKNOWN}

    def accepts(self, position: Position) -> bool:
        """Whether a player at ``position`` is eligible for this slot."""
        return position in SLOT_ELIGIBILITY.get(self, frozenset())


SLOT_ELIGIBILITY: dict[RosterSlot, frozenset[Position]] = {
    RosterSlot.QB: frozenset({Position.QB}),
    RosterSlot.RB: frozenset({Position.RB}),
    RosterSlot.WR: frozenset({Position.WR}),
    RosterSlot.TE: frozenset({Position.TE}),
    RosterSlot.K: frozenset({Position.K}),
    RosterSlot.DEF: frozenset({Position.DEF}),
    RosterSlot.FLEX: frozenset({Position.RB, Position.WR, Position.TE}),
    RosterSlot.REC_FLEX: frozenset({Position.WR, Position.TE}),
    RosterSlot.WRRB_FLEX: frozenset({Position.WR, Position.RB}),
    RosterSlot.SUPER_FLEX: frozenset({Position.QB, Position.RB, Position.WR, Position.TE}),
    RosterSlot.BENCH: frozenset(
        {Position.QB, Position.RB, Position.WR, Position.TE, Position.K, Position.DEF}
    ),
    RosterSlot.IR: frozenset(),
    RosterSlot.TAXI: frozenset(),
    RosterSlot.UNKNOWN: frozenset(),
}


@unique
class ScoringFormat(StrEnum):
    """Reception-scoring family. Custom scoring is carried separately."""

    STANDARD = "standard"
    HALF_PPR = "half_ppr"
    PPR = "ppr"

    @classmethod
    def parse(cls, raw: str | None) -> ScoringFormat:
        """Map Sleeper's ``metadata.scoring_type`` onto the domain enum."""
        if not raw:
            return cls.HALF_PPR
        key = raw.strip().lower().replace("-", "_")
        aliases = {
            "std": cls.STANDARD,
            "standard": cls.STANDARD,
            "half_ppr": cls.HALF_PPR,
            "halfppr": cls.HALF_PPR,
            "half": cls.HALF_PPR,
            "ppr": cls.PPR,
            "full_ppr": cls.PPR,
            "dynasty_ppr": cls.PPR,
            "dynasty_std": cls.STANDARD,
            "dynasty_half_ppr": cls.HALF_PPR,
            "2qb": cls.HALF_PPR,
        }
        return aliases.get(key, cls.HALF_PPR)

    @property
    def points_per_reception(self) -> float:
        """Reception value implied by the format."""
        return {
            ScoringFormat.STANDARD: 0.0,
            ScoringFormat.HALF_PPR: 0.5,
            ScoringFormat.PPR: 1.0,
        }[self]


@unique
class DraftType(StrEnum):
    """Pick-ordering scheme."""

    SNAKE = "snake"
    LINEAR = "linear"
    AUCTION = "auction"

    @classmethod
    def parse(cls, raw: str | None) -> DraftType:
        """Map Sleeper's draft ``type`` onto the domain enum."""
        if not raw:
            return cls.SNAKE
        key = raw.strip().lower()
        return {"snake": cls.SNAKE, "linear": cls.LINEAR, "auction": cls.AUCTION}.get(
            key, cls.SNAKE
        )


@unique
class DraftStatus(StrEnum):
    """Lifecycle of a draft as reported by the provider."""

    PRE_DRAFT = "pre_draft"
    DRAFTING = "drafting"
    PAUSED = "paused"
    COMPLETE = "complete"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: str | None) -> DraftStatus:
        """Map a provider draft status onto the domain enum."""
        if not raw:
            return cls.UNKNOWN
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return cls.UNKNOWN


@unique
class InjuryDesignation(StrEnum):
    """Official game-status designation, plus roster designations.

    ``ACTIVE`` means "no designation reported", which is not the same as
    "verified healthy" - absence of a report is not evidence of health.
    """

    ACTIVE = "ACTIVE"
    QUESTIONABLE = "QUESTIONABLE"
    DOUBTFUL = "DOUBTFUL"
    OUT = "OUT"
    IR = "IR"
    PUP = "PUP"
    NFI = "NFI"
    SUSPENDED = "SUSPENDED"
    COVID = "COVID"
    DID_NOT_REPORT = "DID_NOT_REPORT"
    NOT_ACTIVE = "NOT_ACTIVE"
    UNKNOWN = "UNKNOWN"

    @property
    def rules_out_the_week(self) -> bool:
        """Whether the designation means the player cannot play this week."""
        return self in {
            InjuryDesignation.OUT,
            InjuryDesignation.IR,
            InjuryDesignation.PUP,
            InjuryDesignation.NFI,
            InjuryDesignation.SUSPENDED,
            InjuryDesignation.DID_NOT_REPORT,
            InjuryDesignation.NOT_ACTIVE,
        }


@unique
class PracticeStatus(StrEnum):
    """Normalised practice participation."""

    DNP = "DNP"
    LIMITED = "LIMITED"
    FULL = "FULL"
    UNKNOWN = "UNKNOWN"

    @property
    def severity_rank(self) -> int:
        """Ordinal used for trajectory maths. Higher means more participation."""
        return {
            PracticeStatus.DNP: 0,
            PracticeStatus.LIMITED: 1,
            PracticeStatus.FULL: 2,
            PracticeStatus.UNKNOWN: -1,
        }[self]


@unique
class PracticeTrajectory(StrEnum):
    """Direction of practice participation across recent reports."""

    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    WORSENING = "WORSENING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@unique
class RecurrenceClass(StrEnum):
    """How predictive a past injury to a region is of a future one.

    The heuristic health model uses this to answer a question managers ask out
    loud every August: does last season's injury still count against a player?
    For a hamstring the honest answer is yes; for a healed dislocated elbow it
    is mostly no.
    """

    SOFT_TISSUE = "SOFT_TISSUE"
    PERSISTENT = "PERSISTENT"
    IMPACT = "IMPACT"
    UNINFORMATIVE = "UNINFORMATIVE"


@unique
class BodyRegion(StrEnum):
    """Controlled injury taxonomy.

    Deliberately coarse: providers report a body part, not a diagnosis, and the
    system must not imply clinical detail the source never supplied.
    """

    HEAD = "HEAD"
    NECK = "NECK"
    SHOULDER = "SHOULDER"
    ARM_ELBOW = "ARM_ELBOW"
    HAND_WRIST_FINGER = "HAND_WRIST_FINGER"
    TORSO_RIBS = "TORSO_RIBS"
    BACK = "BACK"
    HIP_GROIN = "HIP_GROIN"
    HAMSTRING = "HAMSTRING"
    QUADRICEPS = "QUADRICEPS"
    KNEE = "KNEE"
    CALF = "CALF"
    ACHILLES = "ACHILLES"
    ANKLE = "ANKLE"
    FOOT_TOE = "FOOT_TOE"
    ILLNESS = "ILLNESS"
    REST = "REST"
    NON_INJURY = "NON_INJURY"
    UNDISCLOSED = "UNDISCLOSED"
    OTHER_UNKNOWN = "OTHER_UNKNOWN"

    @property
    def recurrence_class(self) -> RecurrenceClass:
        """How much a past injury here predicts the next one."""
        return _RECURRENCE_CLASS.get(self, RecurrenceClass.UNINFORMATIVE)

    @property
    def is_soft_tissue(self) -> bool:
        """Soft-tissue regions, which carry elevated re-aggravation risk.

        Used by the heuristic health model to weight recurrent same-region
        events more heavily than, say, a repeated hand injury.
        """
        return self.recurrence_class is RecurrenceClass.SOFT_TISSUE


# A public injury report names a body part and nothing else: never a mechanism,
# never a diagnosis. So the system cannot know that an elbow was dislocated by a
# helmet rather than worn out over a season. What it *can* use is the one thing
# the region itself carries - how strongly an injury there predicts the next one
# - which is why the classification is named for recurrence rather than for
# cause. Anything stronger would be inventing an external fact.
_RECURRENCE_CLASS: dict[BodyRegion, RecurrenceClass] = {
    # Non-contact strains. The best-established recurrence finding in the
    # literature: a prior hamstring strain is the strongest single predictor of
    # the next one, and the same pattern holds across the muscle group.
    BodyRegion.HAMSTRING: RecurrenceClass.SOFT_TISSUE,
    BodyRegion.QUADRICEPS: RecurrenceClass.SOFT_TISSUE,
    BodyRegion.CALF: RecurrenceClass.SOFT_TISSUE,
    BodyRegion.HIP_GROIN: RecurrenceClass.SOFT_TISSUE,
    BodyRegion.ACHILLES: RecurrenceClass.SOFT_TISSUE,
    # Joints and structures that stay compromised: laxity, degeneration and
    # scar tissue outlive the absence, so the second report is rarely a
    # coincidence.
    BodyRegion.KNEE: RecurrenceClass.PERSISTENT,
    BodyRegion.ANKLE: RecurrenceClass.PERSISTENT,
    BodyRegion.FOOT_TOE: RecurrenceClass.PERSISTENT,
    BodyRegion.BACK: RecurrenceClass.PERSISTENT,
    BodyRegion.SHOULDER: RecurrenceClass.PERSISTENT,
    BodyRegion.NECK: RecurrenceClass.PERSISTENT,
    # Head is deliberately placed with the persistent group even though the
    # taxonomy cannot separate a concussion from a facial cut. Repeat-concussion
    # risk is well documented, and where the coarse mapping forces a choice the
    # health model takes the side that does not understate risk.
    BodyRegion.HEAD: RecurrenceClass.PERSISTENT,
    # Bones and joints that break or dislocate on impact and then heal. A
    # cornerback's helmet is not a property of the player's body, so a healed
    # collarbone says far less about next season than a healed hamstring does.
    BodyRegion.ARM_ELBOW: RecurrenceClass.IMPACT,
    BodyRegion.HAND_WRIST_FINGER: RecurrenceClass.IMPACT,
    BodyRegion.TORSO_RIBS: RecurrenceClass.IMPACT,
    # Illness and unlabelled reports carry real absence but no signal about the
    # player's durability, and rest days are not injuries at all.
    BodyRegion.ILLNESS: RecurrenceClass.UNINFORMATIVE,
    BodyRegion.UNDISCLOSED: RecurrenceClass.UNINFORMATIVE,
    BodyRegion.OTHER_UNKNOWN: RecurrenceClass.UNINFORMATIVE,
    BodyRegion.REST: RecurrenceClass.UNINFORMATIVE,
    BodyRegion.NON_INJURY: RecurrenceClass.UNINFORMATIVE,
}


@unique
class Recommendation(StrEnum):
    """The action label surfaced in the war room."""

    DRAFT_NOW = "DRAFT_NOW"
    STRONG_VALUE = "STRONG_VALUE"
    LIKELY_AVAILABLE_LATER = "LIKELY_AVAILABLE_LATER"
    REACH = "REACH"
    DISCOUNT_RISK = "DISCOUNT_RISK"
    AVOID = "AVOID"


@unique
class DataFreshness(StrEnum):
    """How current a displayed metric is, relative to its expected cadence."""

    LIVE = "LIVE"
    FRESH = "FRESH"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    MISSING = "MISSING"


@unique
class ConnectionState(StrEnum):
    """Real-time transport state shown in the war room header."""

    LIVE = "LIVE"
    RECONNECTING = "RECONNECTING"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
