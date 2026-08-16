from nfl_fantasy.draft import choose_pick, overall_pick_number, rank_board
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


def test_rank_board_is_sorted_and_filtered():
    ranked = rank_board(DraftState(round=1, pick=4), STRATEGY, LEAGUE, BOARD)
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(p.position not in {"QB", "K"} for p, _ in ranked)  # both gated in round 1
