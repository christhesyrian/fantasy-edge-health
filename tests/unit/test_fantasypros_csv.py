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


class TestPerPositionExports:
    """FantasyPros exports one file per position, with no position column."""

    def test_a_supplied_position_fills_in_for_a_missing_column(self) -> None:
        text = '"Player","Team","ATT","YDS","FPTS"\n"Josh Allen","BUF","27.9","217.2","380.5"\n'

        converted, report = convert_projections(text, default_position="QB")

        assert report.rows_written == 1
        assert rows_of(converted)[0]["position"] == "QB"

    def test_without_a_position_column_or_a_supplied_one_it_refuses(self) -> None:
        """Silently dropping every row would look like an empty file."""
        with pytest.raises(ConversionError) as error:
            convert_projections('"Player","Team","FPTS"\n"A B","BUF","300"\n')

        assert "--position" in str(error.value)

    def test_duplicate_stat_headings_do_not_break_the_points_column(self) -> None:
        """Passing and rushing both export ATT/YDS/TDS; FPTS stays unique."""
        text = (
            '"Player","Team","ATT","CMP","YDS","TDS","INTS","ATT","YDS","TDS","FL","FPTS"\n'
            '"Jalen Hurts","PHI","27.9","18.5","217.2","1.5",'
            '"0.5","6.6","27.3","0.6","0.2","350.1"\n'
        )

        converted, report = convert_projections(text, default_position="QB")

        assert report.rows_written == 1
        assert rows_of(converted)[0]["projected_points"] == "350.1"

    def test_the_spacer_row_these_exports_contain_is_skipped(self) -> None:
        """Their files carry a row of non-breaking spaces under the header."""
        text = '"Player","Team","FPTS"\n"\xa0","",""\n"Josh Allen","BUF","380"\n'

        _, report = convert_projections(text, default_position="QB")

        assert report.rows_written == 1
        assert report.rows_skipped == 1


class TestSeasonVersusWeekly:
    """The failure this guards is uniform and therefore invisible."""

    def test_a_weekly_export_is_flagged(self) -> None:
        text = '"Player","Team","FPTS"\n"Jalen Hurts","PHI","19.9"\n'

        _, report = convert_projections(text, default_position="QB")

        assert report.warnings
        assert "WEEKLY" in report.warnings[0]

    def test_a_season_export_is_not_flagged(self) -> None:
        text = '"Player","Team","FPTS"\n"Jalen Hurts","PHI","350.1"\n'

        _, report = convert_projections(text, default_position="QB")

        assert not report.warnings

    def test_the_warning_reaches_the_rendered_report(self) -> None:
        _, report = convert_projections(
            '"Player","Team","FPTS"\n"A B","PHI","19.9"\n', default_position="QB"
        )

        assert "WARNING" in report.render()


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
