"""A strategy pointed at a league it does not suit.

Strategies are portable, which is the point and also the risk: `earliest_round`
and `max_per_position` are hard constraints, so the engine obeys them however
badly they fit the format. The balanced strategy gates QB until round 4, which
is right for a single-QB league and wrong for a superflex one -- and nothing
used to say so.
"""

from nfl_fantasy.settings import LeagueSettings, Scoring
from nfl_fantasy.strategy import Strategy

SUPERFLEX = LeagueSettings(
    key="sf", platform="yahoo", league_id="2", teams=12,
    roster_slots=["QB", "WR", "WR", "WR", "RB", "RB", "TE", "FLEX", "SUPER_FLEX",
                  "K", "DST"] + ["BN"] * 5,
)
SINGLE_QB = SUPERFLEX.model_copy(
    update={"roster_slots": [s if s != "SUPER_FLEX" else "BN"
                             for s in SUPERFLEX.roster_slots]}
)

BALANCED = Strategy(
    name="balanced value",
    earliest_round={"QB": 4, "TE": 3, "K": 14, "DST": 13},
    max_per_position={"QB": 2, "TE": 2, "K": 1, "DST": 1},
)


def test_a_qb_gate_is_flagged_in_a_superflex_league_only():
    problems = BALANCED.conflicts_with(SUPERFLEX)
    assert any("gated until round 4" in p and "QB" in p for p in problems)
    # The same strategy against the league it was written for is silent.
    assert BALANCED.conflicts_with(SINGLE_QB) == []


def test_a_cap_below_the_starting_requirement_is_flagged():
    capped = Strategy(name="one qb", max_per_position={"QB": 1})
    problems = capped.conflicts_with(SUPERFLEX)
    assert any("caps QB at 1" in p for p in problems)
    assert capped.conflicts_with(SINGLE_QB) == []


def test_avoiding_qb_early_is_flagged_in_superflex():
    from nfl_fantasy.strategy import RoundPlan

    avoids = Strategy(
        name="skill first",
        round_plan=[RoundPlan(round=1, prefer=["RB", "WR"], avoid=["QB", "K"])],
    )
    assert any("avoids QB" in p for p in avoids.conflicts_with(SUPERFLEX))


def test_a_gate_past_the_end_of_the_draft_is_flagged():
    """A K gated to round 20 in a 16-round draft ends with an empty K slot."""
    late = Strategy(name="no kicker", earliest_round={"K": 20})
    assert any("past the 16-round draft" in p for p in late.conflicts_with(SINGLE_QB))


def test_a_te_gate_is_flagged_in_a_te_premium_league():
    premium = SINGLE_QB.model_copy(
        update={"scoring": Scoring(reception=0.5, te_reception_bonus=0.5)}
    )
    gated = Strategy(name="late te", earliest_round={"TE": 8})
    assert any("TE-premium" in p for p in gated.conflicts_with(premium))
    assert gated.conflicts_with(SINGLE_QB) == []
