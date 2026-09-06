"""Value above replacement, with missed games backfilled."""

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.valuation import (
    SEASON_GAMES,
    adjusted_points,
    allocate_flex,
    dedicated_starters,
    flex_replacement_levels,
    points_per_game,
    replacement_levels,
    value_board,
)

# Cam's Yahoo league: 10 teams, QB/RB/RB/WR/WR/TE/FLEX/K/DST.
LEAGUE = LeagueSettings(
    key="y", platform="yahoo", league_id="1", teams=10,
    roster_slots=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DST"] + ["BN"] * 6,
)


def make(name, position, points, games=SEASON_GAMES):
    return Player(id=name, name=name, position=position,
                  projected_points=points, games=games)


def pool(position, count, top, step):
    return [make(f"{position}{i}", position, top - i * step) for i in range(count)]


BOARD = (
    pool("QB", 30, 380, 6)
    + pool("RB", 60, 300, 3)
    + pool("WR", 70, 270, 2.5)
    + pool("TE", 30, 190, 4)
    + pool("K", 20, 150, 1)
    + pool("DST", 20, 170, 2)
)


def test_points_per_game():
    assert points_per_game(make("a", "QB", 320, 16)) == 20.0
    assert points_per_game(make("b", "QB", 140, 7)) == 20.0
    assert points_per_game(make("c", "DST", 170, None)) is None


def test_missed_games_are_backfilled_not_ignored():
    """A 7-game starter is not worth his total, and not worth a full season."""
    hurt = make("hurt", "QB", 140, 7)  # 20.0 ppg
    replacement_rate = 10.0

    full_credit = 20.0 * SEASON_GAMES          # pretending he plays every week
    face_value = 140.0                          # what the raw total says
    actual = adjusted_points(hurt, replacement_rate)

    assert face_value < actual < full_credit
    # 9 missed weeks covered at replacement rate.
    assert actual == 140.0 + 10.0 * 9


def test_a_full_season_player_is_unchanged():
    healthy = make("ok", "RB", 250, SEASON_GAMES)
    assert adjusted_points(healthy, 9.0) == 250.0


def test_defenses_have_no_games_and_are_taken_at_face_value():
    dst = make("D", "DST", 170, None)
    assert adjusted_points(dst, 9.0) == 170.0


def test_dedicated_starters_scale_by_league_size():
    counts = dedicated_starters(LEAGUE)
    assert counts["RB"] == 20  # two per team, ten teams
    assert counts["QB"] == 10
    assert counts["K"] == 10


def test_flex_is_allocated_to_whoever_has_the_better_player():
    by_position = {}
    for player in BOARD:
        by_position.setdefault(player.position, []).append(player)
    for players in by_position.values():
        players.sort(key=lambda p: p.projected_points, reverse=True)

    allocation = allocate_flex(LEAGUE, by_position, dedicated_starters(LEAGUE))
    added = sum(allocation[p] - dedicated_starters(LEAGUE)[p] for p in ("RB", "WR", "TE"))
    assert added == 10  # one flex slot per team, all handed out
    assert allocation["RB"] > 20 or allocation["WR"] > 20


def test_replacement_sits_deeper_for_positions_that_start_more():
    _, depth = replacement_levels(LEAGUE, BOARD)
    assert depth["RB"] > depth["QB"]
    assert depth["WR"] > depth["QB"]
    assert depth["QB"] == 10


def test_vor_makes_positions_comparable():
    """Raw points say every QB beats every RB. Value above replacement does not."""
    board = value_board(LEAGUE, BOARD)
    best = board[0]

    top_qb = max((v for v in board if v.player.position == "QB"), key=lambda v: v.vor)
    top_rb = max((v for v in board if v.player.position == "RB"), key=lambda v: v.vor)

    # By raw points the QB wins by 80; the replacement QB is nearly as good,
    # so the gap that actually matters is far smaller.
    assert top_qb.points > top_rb.points
    assert top_qb.vor - top_rb.vor < top_qb.points - top_rb.points
    assert best.vor >= top_qb.vor


def test_board_is_sorted_by_vor():
    board = value_board(LEAGUE, BOARD)
    assert [v.vor for v in board] == sorted((v.vor for v in board), reverse=True)


def test_replacement_level_player_is_worth_about_nothing():
    board = value_board(LEAGUE, BOARD)
    _, depth = replacement_levels(LEAGUE, BOARD)
    rbs = sorted((v for v in board if v.player.position == "RB"),
                 key=lambda v: v.vor, reverse=True)
    assert abs(rbs[depth["RB"]].vor) < 1e-6


# -- superflex ---------------------------------------------------------------
#
# Cam's final Yahoo league is 12-team half-PPR with both a W/R/T and a Q/W/R/T
# seat. Every test below covers a way the model got that league wrong when it
# assumed one kind of flex slot, hardcoded to RB/WR/TE.

SUPERFLEX = LeagueSettings(
    key="sf", platform="yahoo", league_id="2", teams=12,
    roster_slots=["QB", "WR", "WR", "WR", "RB", "RB", "TE", "FLEX", "SUPER_FLEX",
                  "K", "DST"] + ["BN"] * 5,
)


def sorted_by_position(board):
    by_position = {}
    for player in board:
        by_position.setdefault(player.position, []).append(player)
    for players in by_position.values():
        players.sort(key=lambda p: p.projected_points, reverse=True)
    return by_position


def test_a_superflex_seat_is_allocated_to_quarterbacks():
    """The seat goes to whoever scores most in it, and that is a quarterback.

    Every flex slot used to draw from one hardcoded RB/WR/TE list, so the twelve
    Q/W/R/T seats were handed to backs and receivers -- pushing their
    replacement twelve places deeper while quarterbacks kept the replacement
    level of a league that starts one.
    """
    allocation = allocate_flex(SUPERFLEX, sorted_by_position(BOARD),
                               dedicated_starters(SUPERFLEX))
    # Twelve dedicated QB slots plus the twelve superflex seats.
    assert allocation["QB"] == 24
    # The W/R/T seats are still the skill positions', and only those.
    added = sum(allocation[p] - dedicated_starters(SUPERFLEX)[p]
                for p in ("RB", "WR", "TE"))
    assert added == 12


def test_superflex_replacement_is_the_second_quarterback_not_the_first():
    levels, depth = replacement_levels(SUPERFLEX, BOARD)
    assert depth["QB"] == 24
    single_qb = SUPERFLEX.model_copy(
        update={"roster_slots": [s if s != "SUPER_FLEX" else "BN"
                                 for s in SUPERFLEX.roster_slots]}
    )
    assert replacement_levels(single_qb, BOARD)[1]["QB"] == 12
    # A deeper replacement is a lower bar, so every quarterback is worth more.
    assert levels["QB"] < replacement_levels(single_qb, BOARD)[0]["QB"]


def test_quarterbacks_reach_the_top_of_a_superflex_board():
    """The symptom that made this findable: no QB anywhere near the top.

    With twenty-four starting, the QB1's lead over the quarterback you would
    otherwise settle for is worth as much as any back's -- which is the whole
    reason the format exists.
    """
    top = [v.player.position for v in value_board(SUPERFLEX, BOARD)[:5]]
    assert "QB" in top

    single_qb = SUPERFLEX.model_copy(
        update={"roster_slots": [s if s != "SUPER_FLEX" else "BN"
                                 for s in SUPERFLEX.roster_slots]}
    )
    # Same board, same strategy, one slot different: the QB1 must rank higher
    # in the superflex league than in the single-QB one.
    def qb1_rank(settings):
        board = value_board(settings, BOARD)
        return next(i for i, v in enumerate(board) if v.player.position == "QB")

    assert qb1_rank(SUPERFLEX) < qb1_rank(single_qb)


def test_each_kind_of_flex_seat_gets_its_own_pooled_replacement():
    """Two seats, two baselines. Sharing one puts QBs on a receiver's scale.

    The Q/W/R/T pool includes quarterbacks and is cut where *they* run out, so
    it sits far above the W/R/T pool. Measuring a quarterback against the
    latter credits him with the gap between two unrelated scales -- hundreds of
    phantom points.
    """
    levels = flex_replacement_levels(SUPERFLEX, BOARD)
    assert set(levels) == {"FLEX", "SUPER_FLEX"}
    assert levels["SUPER_FLEX"] > levels["FLEX"]

    board = {v.player.name: v for v in value_board(SUPERFLEX, BOARD)}
    qb1 = board["QB0"]
    # Valued in the seat he would actually take, the QB1 is worth his lead over
    # the next startable quarterback -- not over a replacement receiver.
    assert qb1.flex_vor("SUPER_FLEX") < qb1.flex_vor("FLEX")
    assert qb1.flex_vor("SUPER_FLEX") == qb1.adjusted - levels["SUPER_FLEX"]


def test_the_flex_bar_is_the_best_player_left_not_a_merged_rank():
    """A league starts ten tight ends whether or not ten are worth starting.

    The old cut merged RB/WR/TE by points and indexed at (dedicated + flex) x
    teams, which assumes the league's starters *are* the top of that merged
    list. They are not: a mandatory TE slot forces ten tight ends into lineups
    while better receivers sit, so the index lands further down the receiver
    list than the real cut. Here that put the flex bar at 202.5 when the best
    player nobody has to start is worth 216.0 -- a bar 13.5 points too low,
    which inflated every flex candidate against dedicated and bench ones.
    """
    levels = flex_replacement_levels(LEAGUE, BOARD)
    assert set(levels) == {"FLEX"}

    depth = allocate_flex(LEAGUE, sorted_by_position(BOARD),
                          dedicated_starters(LEAGUE))
    starting = {p.name for position in ("RB", "WR", "TE")
                for p in sorted_by_position(BOARD)[position][:depth[position]]}
    best_left = max(p.projected_points for p in BOARD
                    if p.position in ("RB", "WR", "TE") and p.name not in starting)
    assert levels["FLEX"] == best_left

    merged = sorted((p.projected_points for p in BOARD
                     if p.position in ("RB", "WR", "TE")), reverse=True)
    assert levels["FLEX"] > merged[(2 + 2 + 1 + 1) * LEAGUE.teams]
