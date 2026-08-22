"""Controlled injury taxonomy and the mapping from raw provider text.

Design rules:

* **Word-boundary matching, never substring.**  ``"chest"`` contains ``"rest"``;
  a naive ``in`` test would classify a rib injury as a rest day.
* **Non-injury reasons are checked before body parts.**  ``"Not injury related -
  resting player"`` is a coach's decision, not a leg injury.
* **Multi-part descriptors are preserved.**  ``"back, ankle, knee"`` yields three
  regions; the first is treated as primary.
* **Nothing is invented.**  The taxonomy records the body *region* a provider
  named.  It never infers severity, diagnosis, or expected recovery time, because
  no source supplies that.

The keyword table is validated against the real observed vocabulary by
``tests/unit/test_injury_taxonomy.py``, which asserts coverage against
``data/fixtures/nflverse_injury_descriptors.json``.
"""

from __future__ import annotations

import re
from typing import Final

from fhe.core.types import BodyRegion, InjuryDesignation

# Laterality and other qualifiers that carry no taxonomic information.
_NOISE_WORDS: Final = re.compile(
    r"\b(left|right|bilateral|lower|upper|mid|the|a|an|of|his)\b", re.IGNORECASE
)
_NON_ALNUM: Final = re.compile(r"[^a-z0-9\s,/+&-]")
_WHITESPACE: Final = re.compile(r"\s+")
_SPLIT_PARTS: Final = re.compile(r"\s*(?:,|/|\+|&|-|\band\b)\s*")
_SEPARATORS: Final = re.compile(r"[-/+&]")


def _rule(*keywords: str) -> re.Pattern[str]:
    """Compile keywords into a single word-boundary alternation."""
    alternation = "|".join(re.escape(k) for k in keywords)
    return re.compile(rf"\b(?:{alternation})\b")


# Checked before body parts, against the *whole* cleaned string.
# Order matters: a rest day and a personal matter are both "not injury related".
_NON_INJURY_RULES: Final[tuple[tuple[re.Pattern[str], BodyRegion], ...]] = (
    (
        _rule(
            "rest",
            "resting",
            "restin",
            "load management",
            "coach",
            "coaches",
            "coachs",
            "coaching",
        ),
        BodyRegion.REST,
    ),
    (
        _rule(
            "personal",
            "travel",
            "suspension",
            "suspended",
            "birth",
            "family",
            "bereavement",
            "funeral",
            "contract",
            "holdout",
            "paternity",
            "jury",
        ),
        BodyRegion.NON_INJURY,
    ),
    (_rule("not injury related", "non football injury", "nfi", "nir"), BodyRegion.NON_INJURY),
)

# Checked per part. Ordered most-specific first so "achilles" wins over "heel"
# and "lower leg" resolves before the bare "leg" fallback.
_BODY_PART_RULES: Final[tuple[tuple[re.Pattern[str], BodyRegion], ...]] = (
    (_rule("achilles"), BodyRegion.ACHILLES),
    (
        _rule(
            "concussion",
            "head",
            "jaw",
            "tooth",
            "teeth",
            "eye",
            "eyes",
            "face",
            "nose",
            "skull",
            "lip",
            "lips",
            "mouth",
            "ear",
            "headache",
        ),
        BodyRegion.HEAD,
    ),
    (_rule("neck", "stinger", "burner", "throat", "cervical", "whiplash"), BodyRegion.NECK),
    (
        _rule(
            "shoulder",
            "shoulders",
            "collarbone",
            "clavicle",
            "sternoclavicular",
            "rotator",
            "deltoid",
            "labrum",
            "ac joint",
            "trap",
            "trapezius",
        ),
        BodyRegion.SHOULDER,
    ),
    (
        _rule("elbow", "forearm", "biceps", "bicep", "triceps", "tricep", "humerus", "arm"),
        BodyRegion.ARM_ELBOW,
    ),
    (
        _rule(
            "hand",
            "hands",
            "wrist",
            "wrists",
            "finger",
            "fingers",
            "thumb",
            "thumbs",
            "knuckle",
        ),
        BodyRegion.HAND_WRIST_FINGER,
    ),
    (
        _rule(
            "rib",
            "ribs",
            "chest",
            "pectoral",
            "pec",
            "oblique",
            "abdomen",
            "abdominal",
            "core",
            "sternum",
            "lung",
            "lungs",
            "kidney",
            "spleen",
            "stomach",
            "torso",
            "liver",
            "appendix",
            "appendicitis",
            "hernia",
        ),
        BodyRegion.TORSO_RIBS,
    ),
    (_rule("back", "spine", "lumbar", "disc", "spinal"), BodyRegion.BACK),
    (_rule("hip", "hips", "groin", "pelvis", "glute", "glutes", "adductor"), BodyRegion.HIP_GROIN),
    (_rule("hamstring", "hamstrings"), BodyRegion.HAMSTRING),
    (_rule("quad", "quads", "quadricep", "quadriceps", "thigh", "thighs"), BodyRegion.QUADRICEPS),
    (
        _rule("knee", "knees", "acl", "mcl", "pcl", "lcl", "meniscus", "patella", "patellar"),
        BodyRegion.KNEE,
    ),
    (_rule("calf", "calves", "shin", "shins", "tibia", "fibula", "leg", "legs"), BodyRegion.CALF),
    (_rule("ankle", "ankles", "high ankle"), BodyRegion.ANKLE),
    (
        _rule("foot", "feet", "toe", "toes", "heel", "plantar", "midfoot", "lisfranc", "arch"),
        BodyRegion.FOOT_TOE,
    ),
    (
        _rule(
            "illness",
            "ill",
            "covid",
            "virus",
            "flu",
            "sick",
            "infection",
            "fever",
            "migraine",
            "heat",
            "dehydration",
        ),
        BodyRegion.ILLNESS,
    ),
    (_rule("undisclosed", "unspecified", "unknown", "body"), BodyRegion.UNDISCLOSED),
)

_DESIGNATION_ALIASES: Final[dict[str, InjuryDesignation]] = {
    "questionable": InjuryDesignation.QUESTIONABLE,
    "q": InjuryDesignation.QUESTIONABLE,
    "doubtful": InjuryDesignation.DOUBTFUL,
    "d": InjuryDesignation.DOUBTFUL,
    "out": InjuryDesignation.OUT,
    "o": InjuryDesignation.OUT,
    "ir": InjuryDesignation.IR,
    "injured reserve": InjuryDesignation.IR,
    "pup": InjuryDesignation.PUP,
    "nfi": InjuryDesignation.NFI,
    "sus": InjuryDesignation.SUSPENDED,
    "susp": InjuryDesignation.SUSPENDED,
    "suspended": InjuryDesignation.SUSPENDED,
    "cov": InjuryDesignation.COVID,
    "covid": InjuryDesignation.COVID,
    "dnr": InjuryDesignation.DID_NOT_REPORT,
    "na": InjuryDesignation.NOT_ACTIVE,
    "active": InjuryDesignation.ACTIVE,
    "healthy": InjuryDesignation.ACTIVE,
    # Informational marker used by the NFL report; carries no game status.
    "note": InjuryDesignation.UNKNOWN,
}


def _clean(raw: str | None) -> str:
    """Lower-case, strip laterality/noise words, and collapse whitespace."""
    if raw is None:
        return ""
    text = raw.strip().lower()
    if not text:
        return ""
    text = _NON_ALNUM.sub(" ", text)
    text = _NOISE_WORDS.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip(" ,-/+&")


def normalize_body_regions(raw: str | None) -> tuple[BodyRegion, ...]:
    """Map raw injury text to every body region it names.

    Returns an empty tuple when nothing was reported at all, which is different
    from :attr:`BodyRegion.OTHER_UNKNOWN` (text was present but unrecognised).

    Examples:
        >>> normalize_body_regions("right Shoulder")
        (<BodyRegion.SHOULDER: 'SHOULDER'>,)
        >>> normalize_body_regions("Not injury related - resting player")
        (<BodyRegion.REST: 'REST'>,)
        >>> len(normalize_body_regions("back, ankle, knee"))
        3
    """
    text = _clean(raw)
    if not text:
        return ()

    # Separators are flattened for the phrase check so "Non-Football Injury"
    # and "NIR-medical" match the same rules as their spaced spellings.
    phrase_text = _SEPARATORS.sub(" ", text)
    for pattern, region in _NON_INJURY_RULES:
        if pattern.search(phrase_text):
            return (region,)

    regions: list[BodyRegion] = []
    for part in _SPLIT_PARTS.split(text):
        part = part.strip()
        if not part:
            continue
        for pattern, region in _BODY_PART_RULES:
            if pattern.search(part):
                if region not in regions:
                    regions.append(region)
                break

    return tuple(regions) if regions else (BodyRegion.OTHER_UNKNOWN,)


def normalize_body_region(raw: str | None) -> BodyRegion:
    """Map raw injury text to its primary body region.

    Unreported text yields :attr:`BodyRegion.OTHER_UNKNOWN` so callers always get
    a usable enum member; use :func:`normalize_body_regions` when the distinction
    between "nothing reported" and "unrecognised" matters.
    """
    regions = normalize_body_regions(raw)
    return regions[0] if regions else BodyRegion.OTHER_UNKNOWN


def normalize_designation(raw: str | None) -> InjuryDesignation:
    """Map a provider game-status / roster-status string to a designation.

    An unreported status becomes :attr:`InjuryDesignation.ACTIVE`, which means
    "no designation on file" - explicitly *not* a claim that the player is
    verified healthy.
    """
    if raw is None:
        return InjuryDesignation.ACTIVE
    key = raw.strip().lower()
    if not key:
        return InjuryDesignation.ACTIVE
    if key in _DESIGNATION_ALIASES:
        return _DESIGNATION_ALIASES[key]
    collapsed = _WHITESPACE.sub(" ", _NON_ALNUM.sub(" ", key)).strip()
    return _DESIGNATION_ALIASES.get(collapsed, InjuryDesignation.UNKNOWN)
