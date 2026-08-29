"""Value above replacement, with missed games backfilled."""

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.valuation import (
    SEASON_GAMES,
    adjusted_points,
    allocate_flex,
    dedicated_starters,
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
