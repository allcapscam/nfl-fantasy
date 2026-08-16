from nfl_fantasy.draft import choose_pick, overall_pick_number
from nfl_fantasy.platforms.base import DraftState, Player
from nfl_fantasy.strategy import Strategy

BOARD = [
    Player(id="1", name="Early QB", position="QB", adp=3, projected_points=340),
    Player(id="2", name="Top WR", position="WR", adp=4, projected_points=300),
    Player(id="3", name="Top RB", position="RB", adp=5, projected_points=310),
    Player(id="4", name="Late Kicker", position="K", adp=150, projected_points=140),
]

STRATEGY = Strategy.model_validate(
    {
        "league": {"teams": 12, "draft_slot": 4},
        "earliest_round": {"QB": 5, "K": 14},
        "round_plan": [{"round": 1, "prefer": ["WR"], "avoid": ["RB"]}],
        "reach_tolerance": 8,
    }
)


def test_overall_pick_number():
    assert overall_pick_number(1, 4, 12) == 4
    assert overall_pick_number(3, 1, 12) == 25


def test_round_one_respects_position_gate_and_plan():
    state = DraftState(round=1, pick=4, on_the_clock=True)
    pick = choose_pick(state, STRATEGY, BOARD)
    # QB is gated until round 5 and K until 14, so the plan's preferred WR wins
    # over the higher-projected RB.
    assert pick is not None
    assert pick.name == "Top WR"


def test_reach_tolerance_excludes_far_off_players():
    state = DraftState(round=1, pick=1, on_the_clock=True)
    board = [Player(id="9", name="Way Early", position="WR", adp=60, projected_points=400)]
    assert choose_pick(state, STRATEGY, board) is None


def test_already_drafted_players_are_skipped():
    state = DraftState(round=1, pick=4, on_the_clock=True, drafted_player_ids={"2"})
    pick = choose_pick(state, STRATEGY, BOARD)
    assert pick is not None
    assert pick.name == "Top RB"


def test_max_per_position_cap():
    strategy = Strategy.model_validate({"max_per_position": {"WR": 1}, "reach_tolerance": 100})
    state = DraftState(
        round=2,
        pick=1,
        on_the_clock=True,
        my_roster=[Player(id="2", name="Top WR", position="WR")],
    )
    pick = choose_pick(state, strategy, BOARD)
    assert pick is not None
    assert pick.position != "WR"
