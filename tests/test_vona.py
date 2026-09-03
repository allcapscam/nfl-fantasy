"""VONA: opportunity cost, positional runs, and the snake maths."""

from collections import Counter

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.valuation import Valuation
from nfl_fantasy.vona import (
    blend_runs,
    candidates,
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


def test_runs_from_adp_reads_the_front_of_the_market_queue():
    """Roughly one player per pick, taken from the top of what is available."""
    adp = {f"p{i}": 20.0 + i for i in range(12)}
    positions = {f"p{i}": ("RB" if i < 5 else "WR") for i in range(12)}

    runs = runs_from_adp(adp, positions, start=20, end=25)
    assert 4.0 < sum(runs.values()) < 6.0   # about five picks' worth
    assert runs["RB"] > runs["WR"]          # the front of the queue is backs
    assert runs["QB"] == 0.0

    # A wider window consumes more of the board.
    wider = runs_from_adp(adp, positions, start=20, end=30)
    assert sum(wider.values()) > sum(runs.values())
    assert runs_from_adp(adp, positions, start=20, end=20) == dict.fromkeys(
        ("QB", "RB", "WR", "TE", "K", "DST"), 0.0
    )


def test_a_player_already_past_his_adp_is_the_likeliest_to_go():
    """Regression: the window test scored exactly these players as zero.

    A tight end at ADP 26 was still there at pick 33 and went at 27 the moment
    the window opened; a back at ADP 28 went at 34. Both times the model said
    the run at their position was negligible, because their ADP sat *behind*
    the window rather than inside it.
    """
    adp = {"fallen": 26.0} | {f"wr{i}": 40.0 + i for i in range(8)}
    positions = {"fallen": "TE"} | {f"wr{i}": "WR" for i in range(8)}

    # The old rule: 33 < 26 <= 40 is false, so he contributed nothing at all.
    assert not 33 < adp["fallen"] <= 40
    runs = runs_from_adp(adp, positions, start=33, end=40)
    assert runs["TE"] > 0.8


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


# -- the cost arithmetic itself ---------------------------------------------


def test_a_bench_candidate_is_compared_against_a_bench_baseline():
    """Regression: value and baseline were computed on different scales.

    A bench player's value dropped the games backfill while the baseline kept
    it, so every bench candidate showed the same constant gap no matter how good
    he was, and the ranking between them carried no information.
    """
    from nfl_fantasy.platforms.base import Player as P

    def v(name, points, replacement):
        pl = P(id=name, name=name, position="QB", projected_points=points)
        return Valuation(player=pl, points=points, games=11,
                         adjusted=points + 60, replacement=replacement)

    pool = [v(f"QB{i}", 300 - i * 10, 290) for i in range(5)]
    # A quarterback is already rostered, so these are all bench candidates.
    ranked = candidates(pool, {"QB": 2.0}, LEAGUE, roster_counts={"QB": 1})
    gaps = [c.cost_of_waiting for c in ranked]
    assert len({round(g, 6) for g in gaps}) > 1  # they differ from each other
    assert gaps == sorted(gaps, reverse=True)


def test_multipliers_scale_the_player_not_the_gap():
    """Regression: (value - baseline) * bonus also inflated the baseline.

    The premium describes the player. Applying it to the difference credits him
    for a share of the replacement he is being measured against, which nothing
    justifies and which shrinks the effect when the gap is small.
    """
    from nfl_fantasy.platforms.base import Player as P

    pl = P(id="r", name="Rookie", position="WR", projected_points=200)
    val_ = Valuation(player=pl, points=200, games=16, adjusted=200, replacement=180)
    c = candidates([val_], {"WR": 0.0}, LEAGUE)[0]
    c.upside = 1.25

    assert c.adjusted_value == val_.vor * 1.25
    assert c.cost_of_waiting == c.adjusted_value - c.expected_next


def test_the_best_player_still_matches_the_two_pick_proof():
    """The position's best player must reproduce the original derivation."""
    from nfl_fantasy.platforms.base import Player as P

    def v(name, points):
        pl = P(id=name, name=name, position="RB", projected_points=points)
        return Valuation(player=pl, points=points, games=16, adjusted=points, replacement=0)

    pool = [v(f"RB{i}", 100 - i * 10) for i in range(6)]
    run = 2.0
    best = candidates(pool, {"RB": run}, LEAGUE)[0]
    assert best.cost_of_waiting == pool[0].vor - interpolate([x.vor for x in pool], run)


# -- the flex slot is its own scale ------------------------------------------


def test_a_flex_seat_is_valued_on_the_pooled_replacement():
    """Regression from pick 64: positional VOR inverts the flex choice.

    Tight end replacement is far below receiver replacement, so a tight end's
    VOR flatters him. Asked to fill a flex slot the model preferred a 158-point
    tight end to a 176-point receiver -- eighteen fewer points in the lineup
    every week -- because it compared each against his own position's baseline
    rather than against the one seat they were actually competing for.
    """
    from nfl_fantasy.platforms.base import Player as P

    def v(name, position, points, replacement):
        player = P(id=name, name=name, position=position, projected_points=points)
        return Valuation(player=player, points=points, games=16, adjusted=points,
                         replacement=replacement, flex_replacement=150.0)

    tight_end = v("LaPorta", "TE", 158.5, 129.5)
    receiver = v("Washington", "WR", 176.4, 150.9)

    # On positional VOR the tight end looks better. He is not: for one shared
    # seat the only thing that counts is points above what else could fill it.
    assert tight_end.vor > receiver.vor
    assert receiver.flex_vor > tight_end.flex_vor

    # Every dedicated slot is full, so the only seat open is the flex.
    ranked = candidates([tight_end, receiver], {"TE": 1.0, "WR": 1.0}, LEAGUE,
                        roster_counts={"QB": 1, "RB": 2, "WR": 2, "TE": 1},
                        open_slots=["FLEX"])
    assert all(c.role == "flex" for c in ranked)

    # Both are now measured against the same baseline, so their values are
    # directly comparable -- which is what positional VOR destroyed.
    by_name = {c.valuation.player.name: c for c in ranked}
    assert by_name["Washington"].value == receiver.flex_vor
    assert by_name["LaPorta"].value == tight_end.flex_vor
    assert by_name["Washington"].value > by_name["LaPorta"].value

    # Ranking by the steeper drop stays correct for choosing a position; what
    # was broken was comparing drops measured on two different scales.
    assert all(c.expected_next == c.value - c.cost_of_waiting for c in ranked)


def test_slot_role_tells_the_three_seats_apart():
    from nfl_fantasy.vona import slot_role

    empty: dict[str, int] = {}
    assert slot_role("RB", empty, LEAGUE) == "dedicated"
    # Two dedicated RB slots filled, so the third back takes the flex.
    assert slot_role("RB", {"RB": 2}, LEAGUE) == "flex"
    # Flex now occupied by that third back, so a fourth is bench.
    assert slot_role("RB", {"RB": 3}, LEAGUE) == "bench"
    # One starting QB and no flex that accepts him.
    assert slot_role("QB", {"QB": 1}, LEAGUE) == "bench"
    # Real open slots win over counting.
    assert slot_role("WR", {"WR": 9}, LEAGUE, open_slots=["WR"]) == "dedicated"
    assert slot_role("WR", {}, LEAGUE, open_slots=["FLEX"]) == "flex"
    assert slot_role("K", {}, LEAGUE, open_slots=["FLEX"]) == "bench"


def test_players_competing_for_one_flex_seat_share_a_baseline():
    """The two-pick derivation does not survive a shared slot.

    Cost-of-waiting assumes the slot is still open at your next turn. That holds
    when a back and a receiver fill different dedicated slots. It fails when
    both would fill the same flex: taking either one closes it, so the position
    you passed on is a bench player next time, not a starter.

    Measured per position, the tight end below wins on a steeper drop while
    being worth eighteen fewer points in the only seat available -- which is the
    live-draft call this corrects.
    """
    from nfl_fantasy.platforms.base import Player as P

    def v(name, position, points, replacement):
        player = P(id=name, name=name, position=position, projected_points=points)
        return Valuation(player=player, points=points, games=16, adjusted=points,
                         replacement=replacement, flex_replacement=143.4)

    board = [
        v("Washington", "WR", 176.4, 150.9),
        v("Odunze", "WR", 173.9, 150.9),
        v("LaPorta", "TE", 158.5, 129.5),   # steep drop behind him
        v("Spare TE", "TE", 100.0, 129.5),
    ]
    have = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
    ranked = candidates(board, {"WR": 1.0, "TE": 1.0}, LEAGUE, have,
                        open_slots=["FLEX"])

    assert ranked[0].valuation.player.name == "Washington"
    # Every flex candidate is measured against the same pooled alternative.
    flex = [c for c in ranked if c.role == "flex"]
    assert len({round(c.expected_next, 6) for c in flex}) == 1
    # And the tight end's steep positional drop no longer buys him anything.
    laporta = next(c for c in flex if c.valuation.player.name == "LaPorta")
    assert ranked[0].cost_of_waiting > laporta.cost_of_waiting
