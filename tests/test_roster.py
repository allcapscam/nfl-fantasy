from nfl_fantasy.platforms.base import Player
from nfl_fantasy.roster import need_multiplier, needs_by_position, unfilled_slots
from nfl_fantasy.settings import LeagueSettings

LEAGUE = LeagueSettings(
    key="std",
    platform="sleeper",
    league_id="1",
    roster_slots=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"],
)


def p(name: str, position: str) -> Player:
    return Player(id=name, name=name, position=position)


def test_empty_roster_needs_everything():
    assert sorted(set(unfilled_slots([], LEAGUE))) == ["FLEX", "QB", "RB", "TE", "WR"]


def test_dedicated_slots_fill_before_flex():
    roster = [p("a", "RB"), p("b", "RB"), p("c", "RB")]
    # Two RBs take the dedicated slots; the third lands in FLEX.
    assert "FLEX" not in unfilled_slots(roster, LEAGUE)
    assert "RB" not in unfilled_slots(roster, LEAGUE)


def test_needs_by_position_accounts_for_flex():
    needs = needs_by_position([p("a", "RB")], LEAGUE)
    assert needs["RB"] == 2  # one dedicated slot left, plus the flex
    assert needs["QB"] == 1


def test_need_multiplier_rewards_open_slots():
    assert need_multiplier("QB", [], LEAGUE) > 1.0


def test_need_multiplier_penalizes_saturation():
    full = [p("q", "QB"), p("r1", "RB"), p("r2", "RB"), p("r3", "RB"),
            p("w1", "WR"), p("w2", "WR"), p("t", "TE")]
    # Every QB slot is spoken for in a single-QB league.
    assert need_multiplier("QB", full, LEAGUE) < 1.0
