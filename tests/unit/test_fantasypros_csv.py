"""Converting FantasyPros CSV exports into the project's import schema.

The exports differ between projections, rankings, and ADP, and their headings
change over time. These assert the converter copes with the shapes that
actually appear, and — more importantly — that it reports rather than guesses
when it cannot.
"""

from __future__ import annotations

import csv
from io import StringIO

import pytest

from fhe.data.ingest.fantasypros_csv import (
    ConversionError,
    convert_adp,
    convert_projections,
)

pytestmark = pytest.mark.unit


def rows_of(text: str) -> list[dict[str, str]]:
    """Parse converted output back into rows."""
    return list(csv.DictReader(StringIO(text)))


class TestProjections:
    def test_the_common_export_shape(self) -> None:
        text = "Player,Team,POS,FPTS\nJa'Marr Chase,CIN,WR1,312.4\nBijan Robinson,ATL,RB2,298.1\n"

        converted, report = convert_projections(text)
        rows = rows_of(converted)

        assert report.rows_written == 2
        assert rows[0] == {
            "player_name": "Ja'Marr Chase",
            "position": "WR",
            "team": "CIN",
            "projected_points": "312.4",
        }

    def test_a_positional_rank_is_stripped_from_the_position(self) -> None:
        """ "WR1" is a position and a rank; only the position is a position."""
        converted, _ = convert_projections("Player,POS,FPTS\nX Y,WR12,200\n")
        assert rows_of(converted)[0]["position"] == "WR"

    def test_a_team_appended_to_the_name_is_separated(self) -> None:
        converted, _ = convert_projections("PLAYER NAME,POS,FPTS\nJosh Allen BUF,QB,380\n")
        row = rows_of(converted)[0]
        assert row["player_name"] == "Josh Allen"
        assert row["team"] == "BUF"

    def test_headings_are_matched_regardless_of_case_and_spacing(self) -> None:
        converted, report = convert_projections(
            "player_name,Positions,Fantasy Points\nA B,TE,150\n"
        )
        assert report.rows_written == 1
        assert rows_of(converted)[0]["position"] == "TE"

    def test_defences_are_normalised_to_the_import_schema(self) -> None:
        """FantasyPros writes DST; the importer accepts DEF."""
        converted, _ = convert_projections("Player,POS,FPTS\nBears D/ST,DST,120\n")
        assert rows_of(converted)[0]["position"] == "DEF"

    def test_a_row_with_no_usable_number_is_reported_not_dropped(self) -> None:
        _, report = convert_projections("Player,POS,FPTS\nA B,WR,\nC D,WR,200\n")

        assert report.rows_written == 1
        assert report.rows_skipped == 1
        assert any("A B" in example for example in report.skipped_examples)

    def test_a_missing_required_column_names_the_headings_present(self) -> None:
        """The error has to be actionable, not just negative."""
        with pytest.raises(ConversionError) as error:
            convert_projections("Guy,Squad,Score\nA B,CIN,10\n")

        message = str(error.value)
        assert "Guy" in message and "Squad" in message

    def test_the_report_says_which_column_filled_each_field(self) -> None:
        _, report = convert_projections("PLAYER NAME,TEAM,POS,FPTS\nA B,CIN,WR,10\n")

        assert report.column_mapping["projected_points"] == "FPTS"
        assert report.column_mapping["player_name"] == "PLAYER NAME"


class TestAdp:
    def test_an_adp_export(self) -> None:
        text = "Rank,Player,Team,POS,Bye,AVG\n1,Ja'Marr Chase,CIN,WR1,10,1.8\n"

        converted, report = convert_adp(text)
        rows = rows_of(converted)

        assert report.rows_written == 1
        assert rows[0]["player_name"] == "Ja'Marr Chase"
        assert rows[0]["adp"] == "1.8"

    def test_standard_deviation_is_carried_through_when_present(self) -> None:
        """It materially improves the survival model, so it must not be lost."""
        converted, report = convert_adp("Player,POS,ADP,Std Dev\nA B,RB,12.5,4.2\n")

        assert rows_of(converted)[0]["adp_stdev"] == "4.2"
        assert report.column_mapping["adp_stdev"] == "Std Dev"

    def test_a_missing_stdev_column_is_stated_in_the_report(self) -> None:
        _, report = convert_adp("Player,POS,ADP\nA B,RB,12.5\n")

        assert "fallback" in report.column_mapping["adp_stdev"]

    def test_a_zero_or_negative_adp_is_rejected(self) -> None:
        """An ADP of 0 is a blank cell, not the first pick."""
        _, report = convert_adp("Player,POS,ADP\nA B,RB,0\nC D,WR,3\n")

        assert report.rows_written == 1
        assert report.rows_skipped == 1

    def test_commas_in_numbers_are_handled(self) -> None:
        converted, _ = convert_adp('Player,POS,ADP,Sample\nA B,RB,1.5,"4,210"\n')
        assert rows_of(converted)[0]["adp"] == "1.5"


class TestOutputFeedsTheImporter:
    def test_projection_output_has_exactly_the_importer_s_required_columns(self) -> None:
        """The whole point is that the result imports without further editing."""
        converted, _ = convert_projections("Player,Team,POS,FPTS\nA B,CIN,WR,10\n")
        header = set(rows_of(converted)[0])

        assert {"player_name", "position", "projected_points"} <= header

    def test_adp_output_has_exactly_the_importer_s_required_columns(self) -> None:
        converted, _ = convert_adp("Player,Team,POS,AVG\nA B,CIN,WR,10\n")
        header = set(rows_of(converted)[0])

        assert {"player_name", "position", "adp"} <= header
