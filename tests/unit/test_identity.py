"""Tests for player identity resolution."""

from __future__ import annotations

import pytest

from fhe.core.types import Position
from fhe.data.identity import (
    ConflictReason,
    IdentityConflict,
    IdentityResolver,
    NflversePlayerIndex,
    PlayerCrosswalk,
    ResolutionMethod,
    ResolvedIdentity,
    clean_token,
    make_player_uuid,
    normalize_name,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def crosswalk() -> PlayerCrosswalk:
    return PlayerCrosswalk.from_rows(
        [
            {
                "sleeper_id": "2359",
                "gsis_id": "00-0032104",
                "espn_id": "2576336",
                "yahoo_id": "28442",
                "name": "Ameer Abdullah",
                "position": "RB",
                "team": "JAX",
            },
            # An R-generated row where everything but the id is missing.
            {
                "sleeper_id": "9001",
                "gsis_id": "NA",
                "espn_id": "NA",
                "yahoo_id": "NA",
                "name": "Rookie Player",
                "position": "WR",
                "team": "NA",
            },
            {"sleeper_id": "NA", "gsis_id": "00-0099999", "name": "No Sleeper Id"},
        ]
    )


@pytest.fixture
def nflverse_index() -> NflversePlayerIndex:
    return NflversePlayerIndex.build(
        [
            {
                "gsis_id": "00-0032104",
                "display_name": "Ameer Abdullah",
                "position": "RB",
                "latest_team": "JAX",
            },
            {
                "gsis_id": "00-0011111",
                "display_name": "Mike Williams",
                "position": "WR",
                "latest_team": "NYJ",
            },
            {
                "gsis_id": "00-0022222",
                "display_name": "Mike Williams",
                "position": "WR",
                "latest_team": "PIT",
            },
            {
                "gsis_id": "00-0033333",
                "display_name": "Odell Beckham Jr.",
                "position": "WR",
                "latest_team": "BAL",
            },
        ]
    )


class TestNullTokens:
    @pytest.mark.parametrize("value", ["NA", "na", "N/A", "", "  ", "null", "None", "nan"])
    def test_r_style_missing_values_become_none(self, value: str) -> None:
        """Regression: treating "NA" as data creates one giant fake player."""
        assert clean_token(value) is None

    def test_real_values_survive(self) -> None:
        assert clean_token(" 00-0032104 ") == "00-0032104"
        assert clean_token(2359) == "2359"


class TestNameNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Odell Beckham Jr.", "odellbeckham"),
            ("Odell Beckham", "odellbeckham"),
            ("D.K. Metcalf", "dkmetcalf"),
            ("DK Metcalf", "dkmetcalf"),
            ("Amon-Ra St. Brown", "amonrastbrown"),
            ("Marquise Brown III", "marquisebrown"),
        ],
    )
    def test_variants_converge(self, raw: str, expected: str) -> None:
        assert normalize_name(raw) == expected

    def test_accents_are_folded(self) -> None:
        assert normalize_name("Nkeal Harry") == normalize_name("Nkeál Harry")

    def test_empty_input(self) -> None:
        assert normalize_name(None) == ""
        assert normalize_name("") == ""


class TestPlayerUuid:
    def test_is_deterministic(self) -> None:
        first = make_player_uuid(gsis_id="00-0032104", sleeper_id="2359")
        second = make_player_uuid(gsis_id="00-0032104", sleeper_id="2359")
        assert first == second

    def test_is_anchored_on_gsis_not_sleeper(self) -> None:
        """A Sleeper id change must not re-key a player already in the database."""
        assert make_player_uuid(gsis_id="00-0032104", sleeper_id="2359") == make_player_uuid(
            gsis_id="00-0032104", sleeper_id="different"
        )

    def test_falls_back_to_sleeper_id(self) -> None:
        uuid = make_player_uuid(gsis_id=None, sleeper_id="9001")
        assert uuid
        assert uuid != make_player_uuid(gsis_id=None, sleeper_id="9002")

    def test_na_is_not_treated_as_an_identifier(self) -> None:
        with pytest.raises(ValueError, match="without a gsis_id or sleeper_id"):
            make_player_uuid(gsis_id="NA", sleeper_id="NA")

    def test_requires_at_least_one_identifier(self) -> None:
        with pytest.raises(ValueError):
            make_player_uuid(gsis_id=None, sleeper_id=None)


class TestCrosswalk:
    def test_na_rows_do_not_become_entries(self, crosswalk: PlayerCrosswalk) -> None:
        assert len(crosswalk) == 2  # the "NA" sleeper_id row is dropped
        assert crosswalk.by_sleeper_id("NA") is None

    def test_missing_gsis_is_none_not_the_string_na(self, crosswalk: PlayerCrosswalk) -> None:
        entry = crosswalk.by_sleeper_id("9001")
        assert entry is not None
        assert entry.gsis_id is None
        assert "gsis_id" not in entry.external_ids

    def test_external_ids_are_collected(self, crosswalk: PlayerCrosswalk) -> None:
        entry = crosswalk.by_sleeper_id("2359")
        assert entry is not None
        assert entry.external_ids["espn_id"] == "2576336"
        assert entry.external_ids["yahoo_id"] == "28442"


class TestResolution:
    def test_direct_gsis_wins(self, crosswalk: PlayerCrosswalk) -> None:
        resolver = IdentityResolver(crosswalk=crosswalk)
        result = resolver.resolve(
            sleeper_id="2359",
            name="Ameer Abdullah",
            position=Position.RB,
            team="JAX",
            gsis_id="00-0032104",
        )
        assert isinstance(result, ResolvedIdentity)
        assert result.method is ResolutionMethod.DIRECT_GSIS
        assert result.confidence == 1.0

    def test_crosswalk_resolves_when_sleeper_lacks_gsis(self, crosswalk: PlayerCrosswalk) -> None:
        """The route that lifts coverage from ~21% to ~95%."""
        resolver = IdentityResolver(crosswalk=crosswalk)
        result = resolver.resolve(
            sleeper_id="2359", name="Ameer Abdullah", position=Position.RB, team="JAX"
        )
        assert isinstance(result, ResolvedIdentity)
        assert result.method is ResolutionMethod.CROSSWALK
        assert result.gsis_id == "00-0032104"

    def test_name_team_position_fallback(self, nflverse_index: NflversePlayerIndex) -> None:
        resolver = IdentityResolver(nflverse_index=nflverse_index)
        result = resolver.resolve(
            sleeper_id="7777", name="Odell Beckham", position=Position.WR, team="BAL"
        )
        assert isinstance(result, ResolvedIdentity)
        assert result.method is ResolutionMethod.NAME_TEAM_POSITION
        assert result.gsis_id == "00-0033333"

    def test_ambiguous_name_becomes_a_conflict_not_a_guess(
        self, nflverse_index: NflversePlayerIndex
    ) -> None:
        """Two Mike Williamses at WR must never be silently collapsed."""
        resolver = IdentityResolver(nflverse_index=nflverse_index)
        result = resolver.resolve(
            sleeper_id="8888", name="Mike Williams", position=Position.WR, team=None
        )
        assert isinstance(result, IdentityConflict)
        assert result.reason is ConflictReason.AMBIGUOUS_MATCH
        assert len(result.candidate_gsis_ids) == 2

    def test_weak_name_only_match_is_not_auto_applied(
        self, nflverse_index: NflversePlayerIndex
    ) -> None:
        resolver = IdentityResolver(nflverse_index=nflverse_index)
        result = resolver.resolve(
            sleeper_id="8889", name="Odell Beckham", position=Position.WR, team="NYG"
        )
        assert isinstance(result, IdentityConflict)
        assert result.reason is ConflictReason.LOW_CONFIDENCE

    def test_unresolvable_player_still_gets_a_durable_id(
        self, crosswalk: PlayerCrosswalk, nflverse_index: NflversePlayerIndex
    ) -> None:
        """An undrafted August rookie is real even with no nflverse history."""
        resolver = IdentityResolver(crosswalk=crosswalk, nflverse_index=nflverse_index)
        result = resolver.resolve(
            sleeper_id="99999", name="Brand New Rookie", position=Position.WR, team="SEA"
        )
        assert isinstance(result, ResolvedIdentity)
        assert result.method is ResolutionMethod.UNRESOLVED
        assert result.player_uuid
        assert not result.is_anchored_to_nflverse

    def test_resolve_many_reports_methods_and_conflicts(
        self, crosswalk: PlayerCrosswalk, nflverse_index: NflversePlayerIndex
    ) -> None:
        resolver = IdentityResolver(crosswalk=crosswalk, nflverse_index=nflverse_index)
        report = resolver.resolve_many(
            [
                {
                    "player_id": "2359",
                    "full_name": "Ameer Abdullah",
                    "position": "RB",
                    "team": "JAX",
                },
                {"player_id": "8888", "full_name": "Mike Williams", "position": "WR", "team": None},
                {
                    "player_id": "99999",
                    "first_name": "Brand",
                    "last_name": "Rookie",
                    "position": "WR",
                    "team": "SEA",
                },
            ]
        )
        assert report.total == 3
        assert len(report.conflicts) == 1
        assert report.method_counts[ResolutionMethod.CROSSWALK] == 1

    def test_records_without_a_player_id_are_skipped(self) -> None:
        report = IdentityResolver().resolve_many([{"full_name": "No Id"}])
        assert report.total == 0

    def test_resolver_works_with_no_crosswalk_at_all(self) -> None:
        """Degradation: the product still runs without the optional crosswalk."""
        resolver = IdentityResolver()
        result = resolver.resolve(
            sleeper_id="2359", name="Ameer Abdullah", position=Position.RB, team="JAX"
        )
        assert isinstance(result, ResolvedIdentity)
        assert result.method is ResolutionMethod.UNRESOLVED
