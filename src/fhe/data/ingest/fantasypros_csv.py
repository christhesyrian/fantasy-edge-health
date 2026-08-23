"""Convert a FantasyPros CSV export into the project's import schema.

Why a converter rather than a parser
------------------------------------
FantasyPros publishes several different exports — season projections, draft
rankings, ADP — and their column headings differ between them and change over
time. Hard-coding one layout would break silently the first time a heading
moved, and silence is the failure mode this project refuses.

So columns are located by *alias*, case- and punctuation-insensitively, and the
result is **reported**: the caller is told which source column filled each
target field. When a required column cannot be found the error lists every
heading actually present, which turns "it didn't work" into "your file calls it
``AVG``, add that alias" in one read.

Nothing here contacts FantasyPros. It converts a file the user exported
themselves, under their own licence, into ``data/schemas/README.md`` shape so
the existing importer can validate and load it.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from io import StringIO
from typing import Final

from fhe.observability import get_logger

log = get_logger(__name__)

# Recognised headings per target field, strongest first. Compared after
# lower-casing and stripping non-alphanumerics, so "PLAYER NAME", "Player Name"
# and "player_name" all collapse to the same token.
_NAME_ALIASES: Final[tuple[str, ...]] = ("playername", "player", "name")
_POSITION_ALIASES: Final[tuple[str, ...]] = ("pos", "position", "positions", "playerpositions")
_TEAM_ALIASES: Final[tuple[str, ...]] = ("team", "tm", "playerteamid")
_POINTS_ALIASES: Final[tuple[str, ...]] = ("fpts", "fantasypoints", "points", "projpts", "proj")
_ADP_ALIASES: Final[tuple[str, ...]] = ("adp", "avg", "avgpick", "rank", "rk", "overall")
_STDEV_ALIASES: Final[tuple[str, ...]] = ("stdev", "stddev", "std", "sd", "adpstdev")
_BYE_ALIASES: Final[tuple[str, ...]] = ("byeweek", "bye")
_TIER_ALIASES: Final[tuple[str, ...]] = ("tiers", "tier")

# A position cell is often "RB1" or "WR12" — the rank rides along with it.
_POSITION_RANK = re.compile(r"^([A-Za-z]+)\s*\d*$")

# Exports sometimes append the team to the name: "Ja'Marr Chase CIN".
_TRAILING_TEAM = re.compile(r"\s+\(?([A-Z]{2,3})\)?$")

# FantasyPros writes defences as DST; the import schema accepts DEF.
_POSITION_NORMALISATION: Final[dict[str, str]] = {"DST": "DEF", "D": "DEF", "DEFENSE": "DEF"}


def _token(heading: str) -> str:
    """Collapse a heading to a comparable token."""
    return re.sub(r"[^a-z0-9]", "", heading.strip().lower())


def _find(headings: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    """The original heading matching the first alias that is present."""
    for alias in aliases:
        if alias in headings:
            return headings[alias]
    return None


@dataclass
class ConversionReport:
    """What the converter did, so a surprise is visible rather than silent."""

    rows_read: int = 0
    rows_written: int = 0
    rows_skipped: int = 0
    top_score: float = 0.0
    column_mapping: dict[str, str] = field(default_factory=dict)
    skipped_examples: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def note_skip(self, reason: str) -> None:
        """Record a skipped row, keeping a few examples."""
        self.rows_skipped += 1
        if len(self.skipped_examples) < 5:
            self.skipped_examples.append(reason)

    def render(self) -> str:
        """A short human summary."""
        lines = [
            f"read {self.rows_read}, wrote {self.rows_written}, skipped {self.rows_skipped}",
            "column mapping:",
        ]
        lines.extend(f"  {target:22} <- {source}" for target, source in self.column_mapping.items())
        if self.skipped_examples:
            lines.append("skipped examples:")
            lines.extend(f"  {example}" for example in self.skipped_examples)
        for warning in self.warnings:
            lines.append(f"\nWARNING: {warning}")
        return "\n".join(lines)


class ConversionError(ValueError):
    """The file cannot be converted, with the headings that were present."""


def _split_name_and_team(raw_name: str) -> tuple[str, str]:
    """Separate a trailing team code from a player name, if present."""
    name = raw_name.strip().strip('"')
    match = _TRAILING_TEAM.search(name)
    if match:
        return name[: match.start()].strip(), match.group(1).upper()
    return name, ""


def _normalise_position(raw: str) -> str:
    """Strip any positional rank and map defence spellings onto DEF."""
    text = raw.strip().upper()
    match = _POSITION_RANK.match(text)
    if match:
        text = match.group(1)
    return _POSITION_NORMALISATION.get(text, text)


def _number(raw: str | None) -> str:
    """A numeric cell as text, or empty. Commas and '%' are stripped."""
    if raw is None:
        return ""
    text = raw.strip().replace(",", "").replace("%", "").replace("$", "")
    if not text or text in {"-", "--", "N/A", "NA"}:
        return ""
    try:
        float(text)
    except ValueError:
        return ""
    return text


def _headings_of(reader: csv.DictReader[str]) -> dict[str, str]:
    """Map comparable tokens to the original headings."""
    return {_token(h): h for h in (reader.fieldnames or []) if h}


# A season projection for the best player at any position lands in the
# hundreds; a single game lands in the tens. FantasyPros exports both from
# similar-looking pages, and importing weekly numbers as season totals would
# silently produce a board an order of magnitude wrong — every player equally
# so, which is exactly the kind of error that looks plausible. This is the
# threshold above which a file is credibly season-long.
MIN_CREDIBLE_SEASON_TOP_SCORE: Final = 100.0


def convert_projections(text: str, *, default_position: str = "") -> tuple[str, ConversionReport]:
    """Convert a projections export into ``projections.csv`` shape.

    ``default_position`` fills in for the per-position exports, which carry no
    position column because the file itself is the position.

    Returns the converted CSV text and a report of what was mapped.
    """
    reader = csv.DictReader(StringIO(text))
    headings = _headings_of(reader)
    name_col = _find(headings, _NAME_ALIASES)
    points_col = _find(headings, _POINTS_ALIASES)
    if name_col is None or points_col is None:
        raise ConversionError(
            "could not find a player-name and fantasy-points column. Headings present: "
            f"{sorted(headings.values())}. Expected one of {_NAME_ALIASES} and {_POINTS_ALIASES}."
        )
    position_col = _find(headings, _POSITION_ALIASES)
    team_col = _find(headings, _TEAM_ALIASES)

    if position_col is None and not default_position:
        raise ConversionError(
            "this file has no position column, so --position is required. "
            f"Headings present: {sorted(headings.values())}. FantasyPros exports "
            "one file per position, and the file itself is the position."
        )

    report = ConversionReport(
        column_mapping={
            "player_name": name_col,
            "projected_points": points_col,
            "position": position_col or f"(supplied: {default_position})",
            "team": team_col or "(none)",
        }
    )

    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["player_name", "position", "team", "projected_points"])

    for row in reader:
        report.rows_read += 1
        name, embedded_team = _split_name_and_team(row.get(name_col) or "")
        team = (row.get(team_col) or "").strip().upper() if team_col else embedded_team
        position = (
            _normalise_position(row.get(position_col) or "")
            if position_col
            else _normalise_position(default_position)
        )
        points = _number(row.get(points_col))

        if not name:
            report.note_skip("row with no player name")
            continue
        if not position:
            # Position is required by the importer and cannot be guessed from a
            # name, so the row is reported rather than dropped quietly.
            report.note_skip(f"{name}: no position column or value")
            continue
        if not points:
            report.note_skip(f"{name}: no usable value in {points_col!r}")
            continue

        writer.writerow([name, position, team, points])
        report.rows_written += 1
        report.top_score = max(report.top_score, float(points))

    if report.top_score and report.top_score < MIN_CREDIBLE_SEASON_TOP_SCORE:
        report.warnings.append(
            f"The highest projection in this file is {report.top_score:.1f}. A season "
            "total for a leading player is in the hundreds, so this looks like a "
            "WEEKLY export. A draft board needs season-long projections — re-export "
            "with the season view selected."
        )

    log.info(
        "fantasypros_projections_converted",
        read=report.rows_read,
        written=report.rows_written,
        skipped=report.rows_skipped,
        top_score=report.top_score,
    )
    return out.getvalue(), report


def convert_adp(text: str) -> tuple[str, ConversionReport]:
    """Convert a rankings or ADP export into ``adp.csv`` shape."""
    reader = csv.DictReader(StringIO(text))
    headings = _headings_of(reader)
    name_col = _find(headings, _NAME_ALIASES)
    adp_col = _find(headings, _ADP_ALIASES)
    if name_col is None or adp_col is None:
        raise ConversionError(
            "could not find a player-name and ADP column. Headings present: "
            f"{sorted(headings.values())}. Expected one of {_NAME_ALIASES} and {_ADP_ALIASES}."
        )
    position_col = _find(headings, _POSITION_ALIASES)
    team_col = _find(headings, _TEAM_ALIASES)
    stdev_col = _find(headings, _STDEV_ALIASES)

    report = ConversionReport(
        column_mapping={
            "player_name": name_col,
            "adp": adp_col,
            "position": position_col or "(none — inferred per row)",
            "team": team_col or "(none)",
            "adp_stdev": stdev_col or "(none — survival model uses its fallback)",
        }
    )

    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["player_name", "position", "team", "adp", "adp_stdev"])

    for row in reader:
        report.rows_read += 1
        name, embedded_team = _split_name_and_team(row.get(name_col) or "")
        team = (row.get(team_col) or "").strip().upper() if team_col else embedded_team
        position = _normalise_position(row.get(position_col) or "") if position_col else ""
        adp = _number(row.get(adp_col))
        stdev = _number(row.get(stdev_col)) if stdev_col else ""

        if not name:
            report.note_skip("row with no player name")
            continue
        if not position:
            report.note_skip(f"{name}: no position column or value")
            continue
        if not adp or float(adp) <= 0:
            report.note_skip(f"{name}: no usable value in {adp_col!r}")
            continue

        writer.writerow([name, position, team, adp, stdev])
        report.rows_written += 1

    log.info(
        "fantasypros_adp_converted",
        read=report.rows_read,
        written=report.rows_written,
        skipped=report.rows_skipped,
    )
    return out.getvalue(), report
