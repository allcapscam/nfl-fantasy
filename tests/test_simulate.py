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
