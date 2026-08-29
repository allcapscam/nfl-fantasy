from nfl_fantasy.draft import (
    PREFER_BONUS,
    choose_pick,
    overall_pick_number,
    rank_board,
    rank_queue,
    value_from_rank,
)
from nfl_fantasy.platforms.base import DraftState, Player
from nfl_fantasy.settings import LeagueSettings, Scoring
from nfl_fantasy.strategy import Strategy

BOARD = [
    Player(id="1", name="Early QB", position="QB", adp=3, projected_points=340),
    Player(id="2", name="Top WR", position="WR", adp=4, projected_points=300),
    Player(id="3", name="Top RB", position="RB", adp=5, projected_points=310),
    Player(id="4", name="Late Kicker", position="K", adp=150, projected_points=140),
]

LEAGUE = LeagueSettings(
    key="std",
    platform="sleeper",
    league_id="1",
    teams=12,
    roster_slots=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DST", "BN"],
)

STRATEGY = Strategy.model_validate(
    {
        "earliest_round": {"QB": 5, "K": 14},
        "round_plan": [{"round": 1, "prefer": ["WR"], "avoid": ["RB"]}],
        "reach_tolerance": 8,
    }
)


def test_overall_pick_number():
    assert overall_pick_number(1, 4, 12) == 4
    assert overall_pick_number(3, 1, 12) == 25


def test_round_one_respects_position_gate_and_plan():
    state = DraftState(round=1, pick=4)
    pick = choose_pick(state, STRATEGY, LEAGUE, BOARD)
    assert pick is not None and pick.name == "Top WR"


def test_reach_tolerance_excludes_far_off_players():
    state = DraftState(round=1, pick=1)
    board = [Player(id="9", name="Way Early", position="WR", adp=60, projected_points=400)]
    assert choose_pick(state, STRATEGY, LEAGUE, board) is None


def test_already_drafted_players_are_skipped():
    state = DraftState(round=1, pick=4, drafted_player_ids={"2"})
    pick = choose_pick(state, STRATEGY, LEAGUE, BOARD)
    assert pick is not None and pick.name == "Top RB"


def test_max_per_position_cap():
    strategy = Strategy.model_validate({"max_per_position": {"WR": 1}, "reach_tolerance": 100})
    state = DraftState(
        round=2, pick=1, my_roster=[Player(id="2", name="Top WR", position="WR")]
    )
    pick = choose_pick(state, strategy, LEAGUE, BOARD)
    assert pick is not None and pick.position != "WR"


# -- the same strategy behaving differently per league ----------------------

OPEN = Strategy.model_validate({"reach_tolerance": 100})
QB_VS_RB = [
    Player(id="q", name="QB1", position="QB", adp=20, projected_points=300),
    Player(id="r", name="RB1", position="RB", adp=20, projected_points=310),
]

SUPERFLEX = LeagueSettings(
    key="sf",
    platform="yahoo",
    league_id="2",
    teams=12,
    roster_slots=["QB", "RB", "RB", "WR", "WR", "TE", "SUPER_FLEX", "BN"],
)


def test_single_qb_league_takes_the_running_back():
    pick = choose_pick(DraftState(round=3, pick=1), OPEN, LEAGUE, QB_VS_RB)
    assert pick is not None and pick.name == "RB1"


def test_superflex_league_takes_the_quarterback_instead():
    """Same strategy, same board -- the league's roster rules flip the pick."""
    pick = choose_pick(DraftState(round=3, pick=1), OPEN, SUPERFLEX, QB_VS_RB)
    assert pick is not None and pick.name == "QB1"


def test_te_premium_lifts_tight_ends():
    board = [
        Player(id="t", name="TE1", position="TE", adp=20, projected_points=250),
        Player(id="w", name="WR1", position="WR", adp=20, projected_points=260),
    ]
    premium = LEAGUE.model_copy(
        update={"scoring": Scoring(reception=1.0, te_reception_bonus=0.5)}
    )
    assert choose_pick(DraftState(round=3, pick=1), OPEN, LEAGUE, board).name == "WR1"
    assert choose_pick(DraftState(round=3, pick=1), OPEN, premium, board).name == "TE1"


# -- the value curve ---------------------------------------------------------


def test_value_decays_with_rank():
    assert value_from_rank(1) > value_from_rank(10) > value_from_rank(100)


def test_a_preference_bonus_cannot_overturn_the_board():
    """Regression: with a linear curve, +15% was worth ~150 ranks.

    A soft preference should break ties between comparable players, not vault a
    mid-round player over an elite one.
    """
    elite = value_from_rank(1)
    twenty_picks_later = value_from_rank(21) * PREFER_BONUS
    assert twenty_picks_later < elite

    # It should still be decisive between near-equals.
    neighbour = value_from_rank(3) * PREFER_BONUS
    assert neighbour > value_from_rank(1)


# -- the queue ---------------------------------------------------------------

QUEUE_STRATEGY = Strategy.model_validate({"earliest_round": {"TE": 3, "K": 14}})
QUEUE_BOARD = [
    Player(id="a", name="Elite RB", position="RB", adp=1),
    Player(id="b", name="Elite TE", position="TE", adp=17),  # round 2 by rank
    Player(id="c", name="Elite WR", position="WR", adp=20),
    Player(id="d", name="Early K", position="K", adp=30),  # gated to round 14
]


def test_queue_ignores_the_reach_limit():
    """A queue covers every pick, so "too early for this pick" is meaningless."""
    tight = QUEUE_STRATEGY.model_copy(update={"reach_tolerance": 1})
    assert len(rank_queue(tight, LEAGUE, QUEUE_BOARD)) == len(QUEUE_BOARD)


def test_gated_player_is_demoted_not_dropped():
    """Regression: the TE1 vanished from the queue instead of moving later."""
    names = [p.name for p, _ in rank_queue(QUEUE_STRATEGY, LEAGUE, QUEUE_BOARD)]
    assert "Elite TE" in names
    # Demoted behind players ranked above the gate, not left at rank 17.
    assert names.index("Elite TE") > names.index("Elite WR")


def test_kicker_gated_to_round_fourteen_lands_late():
    ranked = rank_queue(QUEUE_STRATEGY, LEAGUE, QUEUE_BOARD)
    assert [p.name for p, _ in ranked][-1] == "Early K"


def test_required_starters_are_promoted_into_the_draft():
    """Regression: consensus ranks bury kickers past the last pick.

    A roster with a K slot has to fill it, so an autodraft queue that never
    reaches a kicker would end the draft with a hole in the lineup.
    """
    # A deep board where every kicker ranks below the end of the draft.
    board = [
        Player(id=f"w{i}", name=f"WR{i}", position="WR", adp=float(i))
        for i in range(1, 200)
    ]
    board.append(Player(id="k", name="Only Kicker", position="K", adp=400.0))
    board.append(Player(id="d", name="Only Defense", position="DST", adp=390.0))

    strategy = Strategy.model_validate({"earliest_round": {"K": 9, "DST": 8}})
    ranked = rank_queue(strategy, LEAGUE, board)
    names = [p.name for p, _ in ranked]

    draft_length = len(LEAGUE.roster_slots) * LEAGUE.teams
    assert names.index("Only Kicker") < draft_length
    assert names.index("Only Defense") < draft_length
    # Promoted to their gate, not to the top of the board.
    assert names.index("Only Kicker") >= (9 - 1) * LEAGUE.teams
    assert names.index("Only Defense") >= (8 - 1) * LEAGUE.teams


def test_gate_beyond_the_end_of_the_draft_is_clamped():
    """A K gated to round 14 in a ten-round league still has to be reachable."""
    board = [
        Player(id=f"w{i}", name=f"WR{i}", position="WR", adp=float(i))
        for i in range(1, 200)
    ]
    board.append(Player(id="k", name="Only Kicker", position="K", adp=400.0))

    strategy = Strategy.model_validate({"earliest_round": {"K": 14}})
    names = [p.name for p, _ in rank_queue(strategy, LEAGUE, board)]
    assert names.index("Only Kicker") < len(LEAGUE.roster_slots) * LEAGUE.teams


def test_rank_board_is_sorted_and_filtered():
    ranked = rank_board(DraftState(round=1, pick=4), STRATEGY, LEAGUE, BOARD)
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(p.position not in {"QB", "K"} for p, _ in ranked)  # both gated in round 1


def test_queue_responds_to_roster_shape():
    """Regression: the queue once scored everyone against an empty roster.

    That made a superflex league and a single-QB league produce byte-identical
    queues, which defeats the point of syncing roster rules per league.
    """
    board = []
    for rank in range(1, 121):
        position = "QB" if rank % 4 == 0 else "WR"
        board.append(
            Player(id=f"p{rank}", name=f"P{rank}", position=position, adp=float(rank))
        )

    single_qb = LeagueSettings(
        key="one", platform="sleeper", league_id="1", teams=12,
        roster_slots=["QB", "WR", "WR", "FLEX"] + ["BN"] * 6,
    )
    superflex = single_qb.model_copy(
        update={"roster_slots": ["QB", "WR", "WR", "SUPER_FLEX"] + ["BN"] * 6}
    )

    def first_qb_slot(settings):
        ordered = rank_queue(Strategy(), settings, board)
        return next(i for i, (p, _) in enumerate(ordered) if p.position == "QB")

    assert first_qb_slot(superflex) < first_qb_slot(single_qb)
