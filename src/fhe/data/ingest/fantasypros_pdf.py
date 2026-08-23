"""Extract a FantasyPros ADP/ranking cheat sheet PDF into import shape.

Why a PDF reader exists at all
------------------------------
The CSV export is the better path and stays the recommended one. But the
website does not always offer it for every view, and a cheat sheet PDF is what
a person can actually get hold of on draft week. Retyping five hundred players
is not a plan, so the sheet is parsed.

What the sheet contains, and what it does not
---------------------------------------------
The ADP/ranking cheat sheet carries an **overall ranked list** and **per-position
lists**. It carries no projected points at all — those live in a different
export — so this produces ADP only, and the board will say projections are
missing until they are supplied separately.

Two details drive the parser:

* The overall list is laid out in newspaper columns, and wide rows sometimes run
  two columns together on one text line. So entries are found by scanning each
  line globally for the ``<rank>. <name>, <TEAM>`` pattern rather than by
  slicing fixed-width columns, which silently loses the merged ones.
* **Free agents have no team** — the line ends at the comma. The team is
  therefore optional, and a missing one is recorded as unknown rather than
  causing the row to be dropped.

Positions come from the per-position lists where possible. Those lists stop at
the top hundred of each position, so anyone deeper has no position on the sheet
and is resolved against the player table instead.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from fhe.observability import get_logger

log = get_logger(__name__)

# `<rank>. <name>, <TEAM>` with the team optional, scanned anywhere on a line so
# that two columns sharing one line both parse.
_ENTRY: Final = re.compile(r"(\d{1,3})\.\s+([^,]+?),\s*([A-Z]{2,3})?(?=\s|$)")

_POSITION_BY_HEADER: Final[dict[str, str]] = {
    "Quarterbacks": "QB",
    "Running Backs": "RB",
    "Wide Receivers": "WR",
    "Tight Ends": "TE",
    "Kickers": "K",
    "Defenses/Special Teams": "DEF",
}

# Column headers that mark the per-position page.
_POSITION_PAGE_MARKERS: Final = ("Quarterbacks", "Running Backs")


class PdfExtractionError(RuntimeError):
    """The PDF could not be read, with why."""


@dataclass(frozen=True, slots=True)
class RankedEntry:
    """One line of the cheat sheet."""

    rank: int
    name: str
    team: str
    position: str = ""


@dataclass
class PdfReport:
    """What was found, so a bad parse is visible rather than silent."""

    overall_entries: int = 0
    positioned_from_sheet: int = 0
    positioned_from_database: int = 0
    unresolved: list[str] = field(default_factory=list)

    def render(self) -> str:
        """A short human summary."""
        lines = [
            f"parsed {self.overall_entries} ranked players",
            f"  position from the sheet's own lists: {self.positioned_from_sheet}",
            f"  position resolved from the player table: {self.positioned_from_database}",
            f"  unresolved (skipped): {len(self.unresolved)}",
        ]
        if self.unresolved:
            lines.append("  examples: " + ", ".join(self.unresolved[:8]))
        return "\n".join(lines)


def pdf_to_text(path: Path, *, page: int | None = None) -> str:
    """Extract text with layout preserved.

    Uses `pdftotext`, which ships with poppler and is already present on this
    machine, rather than adding a PDF library to the runtime for a path that is
    a convenience rather than the supported one.
    """
    command = ["pdftotext", "-layout"]
    if page is not None:
        command += ["-f", str(page), "-l", str(page)]
    command += [str(path), "-"]
    try:
        # A fixed argument vector with no shell, so the path is an argument
        # rather than something that can inject. The only caller-supplied value
        # is the file path, which pdftotext treats as a filename whatever it
        # contains.
        result = subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, timeout=60, check=False
        )
    except FileNotFoundError as error:
        raise PdfExtractionError(
            "`pdftotext` is not installed. Install poppler (`brew install poppler`), "
            "or export a CSV from FantasyPros instead, which is the better path."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise PdfExtractionError(f"pdftotext timed out reading {path}") from error
    if result.returncode != 0:
        raise PdfExtractionError(f"pdftotext failed on {path}: {result.stderr.strip()}")
    return result.stdout


def parse_overall(text: str) -> dict[int, RankedEntry]:
    """Every ranked entry on the overall page, keyed by rank."""
    found: dict[int, RankedEntry] = {}
    for line in text.split("\n"):
        for match in _ENTRY.finditer(line):
            rank = int(match.group(1))
            # A later page repeats rank numbers per position; the overall page
            # is parsed alone, so first-seen wins and stays stable.
            found.setdefault(
                rank,
                RankedEntry(
                    rank=rank,
                    name=match.group(2).strip(),
                    team=(match.group(3) or "").upper(),
                ),
            )
    return found


def parse_positions(text: str) -> dict[tuple[str, str], str]:
    """Map (lowercased name, team) to position from the per-position lists."""
    lines = text.split("\n")
    header_index = next(
        (i for i, line in enumerate(lines) if all(m in line for m in _POSITION_PAGE_MARKERS)),
        None,
    )
    if header_index is None:
        return {}

    header = lines[header_index]
    columns = sorted(
        (header.index(label), position)
        for label, position in _POSITION_BY_HEADER.items()
        if label in header
    )
    bounds = [
        (start, columns[i + 1][0] if i + 1 < len(columns) else len(header) + 10_000, position)
        for i, (start, position) in enumerate(columns)
    ]

    positions: dict[tuple[str, str], str] = {}
    for line in lines[header_index + 1 :]:
        for start, end, position in bounds:
            for match in _ENTRY.finditer(line[start:end]):
                name = match.group(2).strip().lower()
                team = (match.group(3) or "").upper()
                positions[(name, team)] = position
    return positions
