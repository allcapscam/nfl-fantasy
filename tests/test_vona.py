"""VONA: opportunity cost, positional runs, and the snake maths."""

from collections import Counter

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.valuation import Valuation
from nfl_fantasy.vona import (
    blend_runs,
    interpolate,
    missing_required,
    need_weight,
    next_pick_after,
    opportunity_costs,
    roster_cap,
    runs_from_adp,
    runs_from_needs,
    snake_picks,
    team_at_pick,
    team_needs,
)

LEAGUE = LeagueSettings(
    key="y", platform="yahoo", league_id="1", teams=10,
    roster_slots=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DST"] + ["BN"] * 6,
)


def val(name, position, vor):
    player = Player(id=name, name=name, position=position, projected_points=vor)
    return Valuation(player=player, points=vor, games=16, adjusted=vor, replacement=0.0)


# -- snake maths -------------------------------------------------------------


def test_snake_picks_reverse_each_round():
    picks = snake_picks(slot=3, teams=10, rounds=4)
    assert picks == [3, 18, 23, 38]


def test_snake_turn_gives_back_to_back_picks():
    first = snake_picks(slot=1, teams=10, rounds=2)
    last = snake_picks(slot=10, teams=10, rounds=2)
    assert first == [1, 20]
    assert last == [10, 11]  # the turn: two picks one apart


def test_next_pick_after():
    assert next_pick_after(3, slot=3, teams=10, rounds=15) == 18
    assert next_pick_after(18, slot=3, teams=10, rounds=15) == 23
    assert next_pick_after(148, slot=3, teams=10, rounds=15) is None


# -- the expectation ---------------------------------------------------------


def test_interpolate_handles_fractional_runs():
    """3.5 taken means half the time you get the 4th, half the 5th."""
    values = [100.0, 90.0, 80.0, 70.0, 60.0, 50.0]
    assert interpolate(values, 0) == 100.0
    assert interpolate(values, 3) == 70.0
    assert interpolate(values, 3.5) == 65.0  # midpoint of the 4th and 5th
    assert interpolate(values, 99) == 50.0   # never past the end of the board


def test_interpolate_on_an_empty_pool():
    assert interpolate([], 3) == 0.0


# -- opportunity cost --------------------------------------------------------


def test_biggest_drop_wins_not_the_best_player():
    """The headline case: the better player is the wrong pick.

    The receiver grades higher, but almost no receivers go before the next turn
    while the run on backs is severe -- so the back is worth more over two picks.
    """
    available = [
        val("WR1", "WR", 100), val("WR2", "WR", 98), val("WR3", "WR", 96),
        val("RB1", "RB", 95), val("RB2", "RB", 70), val("RB3", "RB", 60),
        val("RB4", "RB", 55),
    ]
    runs = {"WR": 1.0, "RB": 3.0}

    costs = opportunity_costs(available, runs, LEAGUE)
    assert costs[0].position == "RB"

    # And the ranking really is the two-pick total, not a heuristic.
    take_rb = 95 + interpolate([100, 98, 96], 1.0)
    take_wr = 100 + interpolate([95, 70, 60, 55], 3.0)
    assert take_rb > take_wr


def test_no_run_means_no_urgency():
    available = [val("WR1", "WR", 100), val("WR2", "WR", 99),
                 val("RB1", "RB", 95), val("RB2", "RB", 40)]
    # Nobody is taking a receiver, so the receiver keeps.
    costs = opportunity_costs(available, {"WR": 0.0, "RB": 1.0}, LEAGUE)
    assert costs[0].position == "RB"

    # Now the receivers are the ones running out.
    costs = opportunity_costs(available, {"WR": 1.0, "RB": 0.0}, LEAGUE)
    assert costs[0].position == "WR"


def test_saturated_positions_stop_competing():
    """Once you cannot start another, it is a bench flyer, not an opportunity."""
    available = [val("QB1", "QB", 200), val("QB2", "QB", 10),
                 val("RB1", "RB", 50), val("RB2", "RB", 40)]
    runs = {"QB": 1.0, "RB": 1.0}

    assert opportunity_costs(available, runs, LEAGUE)[0].position == "QB"
    # One starting QB in this league, so a third is off the board.
    loaded = {"QB": 2, "RB": 0}
    positions = [o.position for o in opportunity_costs(available, runs, LEAGUE, loaded)]
    assert "QB" not in positions


# -- estimating the run ------------------------------------------------------


def test_runs_from_adp_counts_the_window():
    adp = {"a": 5.0, "b": 12.0, "c": 14.0, "d": 25.0}
    positions = {"a": "RB", "b": "RB", "c": "WR", "d": "WR"}
    runs = runs_from_adp(adp, positions, start=10, end=20)
    assert runs["RB"] == 1.0  # only b falls in (10, 20]
    assert runs["WR"] == 1.0  # only c
    assert runs["QB"] == 0.0


def test_team_at_pick_follows_the_snake():
    assert [team_at_pick(p, 10) for p in range(1, 11)] == list(range(1, 11))
    # Round two runs backwards.
    assert [team_at_pick(p, 10) for p in range(11, 21)] == list(range(10, 0, -1))


def test_team_needs_shrink_as_slots_fill():
    empty = team_needs(LEAGUE, Counter())
    assert empty["RB"] == 2 and empty["WR"] == 2 and empty["QB"] == 1

    stocked = team_needs(LEAGUE, Counter({"RB": 2, "WR": 2, "TE": 1, "QB": 1}))
    assert stocked["QB"] == 0
    # Dedicated slots are full, so what is left is flex appetite, not RB need.
    assert stocked["RB"] < empty["RB"]


def test_a_room_full_of_backs_stops_wanting_backs():
    """Cam's case: if everyone already drafted their backs, the run is spent.

    A momentum model gets this exactly backwards -- it sees a run on backs and
    predicts more of them, when in fact the demand has been consumed.
    """
    # Two full rounds where every team took a running back.
    all_backs = ["RB"] * 20
    # Same two rounds, but the room spread its picks around.
    mixed = ["WR", "TE", "QB", "WR", "TE"] * 4

    window = (21, 30)
    backs_room = runs_from_needs(LEAGUE, all_backs, *window, my_slot=5)
    mixed_room = runs_from_needs(LEAGUE, mixed, *window, my_slot=5)

    assert backs_room["RB"] < mixed_room["RB"]
    # And with their backs full, those teams now want receivers.
    assert backs_room["WR"] > backs_room["RB"]


def test_need_weight_grows_through_the_draft():
    assert need_weight(1) == 0.0          # round one is best-available
    assert 0 < need_weight(5) < need_weight(9)
    assert need_weight(9) == need_weight(15)  # capped once need dominates


def test_blend_hands_over_from_adp_to_need():
    prior = dict.fromkeys(("QB", "RB", "WR", "TE", "K", "DST"), 0.0) | {"RB": 4.0}
    needs = dict.fromkeys(("QB", "RB", "WR", "TE", "K", "DST"), 0.0) | {"RB": 0.0}

    assert blend_runs(prior, needs, round_number=1)["RB"] == 4.0
    late = blend_runs(prior, needs, round_number=12)["RB"]
    assert late < 1.0  # need model has taken over and says the run is over


# -- the safety net for letting VONA handle K and DST ------------------------


def test_warns_only_when_the_holes_no_longer_fit():
    roster = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 0, "DST": 0}
    # Three picks left, two holes -- still fine.
    assert missing_required(LEAGUE, roster, picks_left=3) == []
    # Two picks left and two holes -- every remaining pick is now forced.
    assert set(missing_required(LEAGUE, roster, picks_left=2)) == {"K", "DST"}


def test_roster_cap_does_not_let_flex_inflate_tight_end_demand():
    """Regression: the model drafted three TEs in a one-TE league.

    max_startable counts the flex slot toward TE, but a flex is rarely spent on
    a third tight end. Depth belongs at the positions you start several of.
    """
    assert roster_cap("TE", LEAGUE) == 2
    assert roster_cap("RB", LEAGUE) == 4
    assert roster_cap("WR", LEAGUE) == 4
    assert roster_cap("QB", LEAGUE) == 2
    assert roster_cap("K", LEAGUE) == 1
    assert roster_cap("DST", LEAGUE) == 1
