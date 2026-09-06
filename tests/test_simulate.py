"""The simulator's objective function.

`lineup_points` is what every opening in the sweep is scored by, so an error
here does not produce a slightly wrong ranking -- it produces a confident answer
to a different question.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import functools

from simulate import lineup_points

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.valuation import Valuation

SUPERFLEX = LeagueSettings(
    key="sf", platform="yahoo", league_id="2", teams=12,
    roster_slots=["QB", "WR", "WR", "WR", "RB", "RB", "TE", "FLEX", "SUPER_FLEX",
                  "K", "DST"] + ["BN"] * 5,
)


def v(name, position, points):
    player = Player(id=name, name=name, position=position, projected_points=points)
    return Valuation(player=player, points=points, games=16, adjusted=points,
                     replacement=0.0, flex_replacement={"FLEX": 0.0,
                                                        "SUPER_FLEX": 0.0})


def test_a_full_lineup_scores_every_starting_slot():
    roster = [
        v("qb1", "QB", 400), v("qb2", "QB", 360),
        v("wr1", "WR", 260), v("wr2", "WR", 240), v("wr3", "WR", 220),
        v("rb1", "RB", 250), v("rb2", "RB", 230), v("rb3", "RB", 210),
        v("te1", "TE", 200), v("k", "K", 140), v("dst", "DST", 130),
    ]
    # Eleven starters, and every one of them is startable here: the second
    # quarterback takes Q/W/R/T and the third back takes W/R/T.
    assert lineup_points(roster, SUPERFLEX) == sum(x.points for x in roster)


def test_a_wider_seat_must_not_strand_a_quarterback():
    """The ordering bug, in the smallest roster that shows it.

    `Q/W/R/T` was grouped with the dedicated slots, so it filled before `W/R/T`
    and took the best player it could -- a tight end. The plain flex then took
    the next tight end, and the second quarterback, who had nowhere else to go,
    went unstarted. Filling the choosier seat first leaves the wider one for the
    player only it can hold.
    """
    roster = [
        v("wr1", "WR", 110),
        v("te1", "TE", 383), v("te2", "TE", 280), v("te3", "TE", 157),
        v("qb1", "QB", 299), v("qb2", "QB", 249),
        v("k1", "K", 82),
    ]
    # QB 299 + WR 110 + TE 383 + K 82 + FLEX(TE 280) + SUPER_FLEX(QB 249).
    assert lineup_points(roster, SUPERFLEX) == 1403


def test_the_lineup_is_the_best_one_available_not_merely_a_legal_one():
    """A property check against an exact solve, because this is the objective.

    Every opening in the sweep is ranked by this number, so a greedy that is
    sometimes suboptimal does not blur the ranking evenly -- it penalises the
    rosters it happens to mis-fill, which here were the ones carrying a spare
    quarterback. Filling narrowest-seat-first is provably optimal for nested
    eligibility, and this pins it.
    """
    import random

    from nfl_fantasy.settings import slot_accepts

    positions = ("QB", "RB", "WR", "TE", "K", "DST")
    slots = SUPERFLEX.starting_slots

    def exact(roster):
        by = {p: sorted((x.points for x in roster if x.player.position == p),
                        reverse=True) for p in positions}

        @functools.cache
        def best(index, used):
            if index == len(slots):
                return 0.0
            slot = slots[index]
            out = best(index + 1, used)          # leaving the seat empty
            for j, position in enumerate(positions):
                if not slot_accepts(slot, position) or used[j] >= len(by[position]):
                    continue
                nxt = list(used)
                nxt[j] += 1
                out = max(out, by[position][used[j]] + best(index + 1, tuple(nxt)))
            return out

        return best(0, (0,) * len(positions))

    rng = random.Random(2)
    for _ in range(300):
        roster = [v(f"p{i}", rng.choice(positions), rng.randint(50, 400))
                  for i in range(rng.randint(5, 16))]
        assert lineup_points(roster, SUPERFLEX) == exact(roster)


def test_a_second_quarterback_is_worth_nothing_without_the_superflex_seat():
    single_qb = SUPERFLEX.model_copy(
        update={"roster_slots": [s if s != "SUPER_FLEX" else "BN"
                                 for s in SUPERFLEX.roster_slots]}
    )
    roster = [
        v("qb1", "QB", 400), v("qb2", "QB", 360),
        v("wr1", "WR", 260), v("wr2", "WR", 240), v("wr3", "WR", 220),
        v("rb1", "RB", 250), v("rb2", "RB", 230), v("te1", "TE", 200),
    ]
    with_seat = lineup_points(roster, SUPERFLEX)
    without = lineup_points(roster, single_qb)
    # In the superflex league the QB2 starts and the flex is still open for him
    # to be joined; in the single-QB league he is a bench player worth nothing.
    assert with_seat - without == 360


def test_only_the_best_eligible_players_start():
    roster = [v(f"wr{i}", "WR", 200 - i) for i in range(8)]
    # Three dedicated WR slots plus both flex seats can take receivers.
    assert lineup_points(roster, SUPERFLEX) == sum(200 - i for i in range(5))


def test_the_room_takes_defences_and_kickers_on_their_own_schedules():
    """One gate for both mispriced whichever gap it landed in.

    Rooms treat the two separately -- a streaming defence has visible upside and
    goes rounds before a kicker, who is close to interchangeable. Holding both
    at one number means either the best defences are still on the board when the
    room has already taken them, or kickers vanish while they are in fact there.
    """
    import random

    import simulate

    settings = SUPERFLEX
    kickers = [v(f"k{i}", "K", 140 - i) for i in range(12)]
    defences = [v(f"dst{i}", "DST", 130 - i) for i in range(12)]
    receivers = [v(f"wr{i}", "WR", 250 - i) for i in range(60)]
    # By the middle rounds the top of the ADP board *is* kickers and defences.
    # That is the condition the gate exists for, so the fixture has to create
    # it: with skill players ahead of them the room never reaches for either
    # and the gate is untested no matter what it is set to.
    for index, item in enumerate(kickers + defences + receivers):
        item.player.adp = float(index + 1)
    board = kickers + defences + receivers

    def first_round_taken(position, **gates):
        simulate.set_kdst_rounds({"DST": 99, "K": 99})
        simulate.set_kdst_rounds(gates)
        rng = random.Random(0)
        roster = []
        for round_number in range(1, 17):
            pick = simulate.opponent_pick(list(board), roster, settings, rng)
            roster.append(pick)
            if pick.player.position == position:
                return round_number
        return None

    try:
        # Defences open at 10 and kickers at 14: each is held until its own gate.
        assert first_round_taken("DST", DST=10, K=14) >= 10
        assert first_round_taken("K", DST=10, K=14) >= 14
        # And moving one does not drag the other with it.
        assert first_round_taken("DST", DST=4, K=14) >= 4
        assert first_round_taken("K", DST=4, K=14) >= 14
    finally:
        simulate.set_kdst_rounds({"DST": 8, "K": 8})
