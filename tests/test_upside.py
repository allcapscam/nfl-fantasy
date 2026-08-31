"""The ceiling premium, and the shortlist that surfaces it."""

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.upside import (
    MAX_UPSIDE_BONUS,
    History,
    describe,
    load_history,
    upside_multiplier,
    upside_weight,
)
from nfl_fantasy.valuation import Valuation
from nfl_fantasy.vona import candidates, diversify

LEAGUE = LeagueSettings(
    key="y", platform="yahoo", league_id="1", teams=10,
    roster_slots=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DST"] + ["BN"] * 6,
)


def val(name, position, vor):
    player = Player(id=name, name=name, position=position, projected_points=vor)
    return Valuation(player=player, points=vor, games=16, adjusted=vor, replacement=0.0)


# -- the premium -------------------------------------------------------------


def test_no_gamble_in_round_one():
    """An established elite player is what you want anchoring a roster."""
    assert upside_weight(1) == 0.0


def test_premium_ramps_in_then_caps():
    assert 0 < upside_weight(3) < upside_weight(5)
    assert upside_weight(6) == MAX_UPSIDE_BONUS
    assert upside_weight(14) == MAX_UPSIDE_BONUS  # capped, never compounds


def test_only_unproven_players_get_it():
    history = {
        "rookie": History(games=0, points=0.0),
        "veteran": History(games=16, points=250.0),
    }
    assert upside_multiplier("rookie", history, 8) > 1.0
    assert upside_multiplier("veteran", history, 8) == 1.0
    # Absent from the file means a full prior season, so no premium.
    assert upside_multiplier("unlisted", history, 8) == 1.0


def test_a_handful_of_games_still_counts_as_unproven():
    history = {"hurt": History(games=3, points=40.0)}
    assert upside_multiplier("hurt", history, 8) > 1.0
    assert describe("hurt", history) == "only 3 games in 2025"
    assert describe("nobody", history) is None


def test_describe_distinguishes_no_season_from_a_short_one():
    history = {"none": History(games=0, points=0.0)}
    assert describe("none", history) == "no 2025 games"


def test_load_history_reads_the_file(tmp_path):
    path = tmp_path / "h.csv"
    path.write_text(
        "name,position,prior_games,prior_points\n"
        "Jeremiyah Love,RB,0,0\nOmarion Hampton,RB,9,119.7\n",
        encoding="utf-8",
    )
    history = load_history(path)
    assert history["jeremiyahlove"].games == 0
    assert history["omarionhampton"].points == 119.7
    assert load_history(tmp_path / "missing.csv") == {}


# -- the shortlist -----------------------------------------------------------

BOARD = [val(f"RB{i}", "RB", 100 - i * 4) for i in range(8)] + [
    val(f"WR{i}", "WR", 80 - i * 3) for i in range(8)
] + [val("TE0", "TE", 50)]


def test_every_player_at_a_position_shares_one_baseline():
    """Regression: deeper players were measured further down the list.

    That assumed the players ahead of them also disappeared. But if you pass on
    the second-best back you take the best one -- you do not wait. Everyone at a
    position is compared against the same thing: whoever is left after the run.
    Deeper players then score lower purely because they are worth less.
    """
    ranked = candidates(BOARD, {"RB": 2.0, "WR": 1.0, "TE": 0.0}, LEAGUE, per_position=3)
    backs = [c for c in ranked if c.position == "RB"]
    assert len(backs) == 3
    assert backs[0].depth == 0 and backs[1].depth == 1

    assert backs[0].expected_next == backs[1].expected_next == backs[2].expected_next
    # Ordering now comes from value alone, which is the honest comparison.
    assert backs[0].cost_of_waiting > backs[1].cost_of_waiting > backs[2].cost_of_waiting


def test_shortlist_always_offers_a_second_position():
    """Four running backs is one recommendation with spares, not a shortlist."""
    # A board where backs dominate every slot on raw cost of waiting.
    lopsided = [val(f"RB{i}", "RB", 200 - i * 30) for i in range(6)] + [
        val("WR0", "WR", 20), val("WR1", "WR", 19)
    ]
    ranked = candidates(lopsided, {"RB": 3.0, "WR": 0.1}, LEAGUE)
    shortlist = diversify(ranked, count=4, min_positions=2)

    assert len(shortlist) == 4
    assert len({c.position for c in shortlist}) >= 2


def test_shortlist_stays_ordered_by_cost():
    ranked = candidates(BOARD, {"RB": 2.0, "WR": 2.0, "TE": 0.5}, LEAGUE)
    shortlist = diversify(ranked, count=4)
    costs = [c.cost_of_waiting for c in shortlist]
    assert costs == sorted(costs, reverse=True)


def test_diversify_handles_a_thin_board():
    only_backs = [val("RB0", "RB", 50), val("RB1", "RB", 40)]
    ranked = candidates(only_backs, {"RB": 1.0}, LEAGUE)
    # Cannot invent a second position that isn't there; returns what exists.
    assert len(diversify(ranked, count=4, min_positions=2)) == 2
    assert diversify([], count=4) == []


# -- the bench discount ------------------------------------------------------


def test_a_player_who_would_not_start_is_discounted():
    """Live-draft regression, twice over.

    The model offered a second quarterback behind an established starter, and
    preferred a fourth running back to a tight end while the TE slot sat empty.
    Both came from valuing production that never enters the lineup.
    """
    from nfl_fantasy.vona import BENCH_VALUE, lineup_multiplier, starts_immediately

    # One starting QB in this league.
    assert starts_immediately("QB", {}, LEAGUE)
    assert not starts_immediately("QB", {"QB": 1}, LEAGUE)
    assert lineup_multiplier("QB", {"QB": 1}, LEAGUE) == BENCH_VALUE

    # Two dedicated RB slots plus the flex, so a fourth back is bench.
    assert starts_immediately("RB", {"RB": 2}, LEAGUE)
    assert not starts_immediately("RB", {"RB": 3}, LEAGUE)


def test_an_empty_slot_beats_a_better_bench_player():
    """The exact call from round 7: RB4 graded higher, but the TE slot was open."""
    board = [
        val("Bench RB", "RB", 24.2),
        val("Starting TE", "TE", 19.3),
        val("Worse RB", "RB", 14.2),
        val("Worse TE", "TE", 12.8),
    ]
    have = {"RB": 3, "TE": 0, "QB": 1, "WR": 2}
    ranked = candidates(board, {"RB": 1.0, "TE": 0.8}, LEAGUE, have)
    assert ranked[0].position == "TE"
    assert ranked[0].starts


def test_the_flex_is_not_double_booked():
    """Regression: a third receiver read as a starter while the flex was full.

    max_startable credits the flex to RB, WR and TE independently. With a third
    back already occupying it, a third receiver is bench -- but the count-based
    check said starter, and the model kept preferring bench receivers to
    genuinely better players.
    """
    from nfl_fantasy.platforms.base import Player as P
    from nfl_fantasy.roster import unfilled_slots
    from nfl_fantasy.vona import starts_immediately

    roster = [
        P(id="q", name="q", position="QB"),
        P(id="r1", name="r1", position="RB"), P(id="r2", name="r2", position="RB"),
        P(id="r3", name="r3", position="RB"),          # this one takes the FLEX
        P(id="w1", name="w1", position="WR"), P(id="w2", name="w2", position="WR"),
        P(id="t", name="t", position="TE"),
    ]
    counts = {"QB": 1, "RB": 3, "WR": 2, "TE": 1}
    open_slots = unfilled_slots(roster, LEAGUE)

    # Counting positions says a third receiver starts. He does not.
    assert starts_immediately("WR", counts, LEAGUE) is True
    assert starts_immediately("WR", counts, LEAGUE, open_slots) is False
    # Only the kicker and defence are genuinely open.
    assert set(open_slots) == {"K", "DST"}


# -- draft-day regressions from the 6572 league ------------------------------


def test_a_bench_player_is_valued_on_real_points_not_backfilled_ones():
    """Regression: Brissett's 215 real points read as 314 as a backup QB.

    The games backfill credits replacement production for missed weeks. That is
    right for a starter you stream around and meaningless for a QB2 -- you
    already have someone in the slot.
    """
    hurt = Valuation(
        player=Player(id="h", name="Hurt QB", position="QB", projected_points=215),
        points=215.0, games=11, adjusted=314.0, replacement=317.0,
    )
    healthy = Valuation(
        player=Player(id="w", name="Healthy QB", position="QB", projected_points=292),
        points=292.0, games=15, adjusted=312.0, replacement=317.0,
    )
    # Backfilled, the injured one looks better. On real points he plainly is not.
    assert hurt.vor > healthy.vor
    assert healthy.bench_vor > hurt.bench_vor


def test_a_crowded_bye_week_is_penalised():
    """Regression: it offered a bye-11 kicker into a week with four starters out."""
    from nfl_fantasy.vona import BYE_CROWDING_PENALTY, bye_conflict

    crowded = {11: 4}
    assert bye_conflict(11, crowded)[0] == BYE_CROWDING_PENALTY
    assert "week 11" in bye_conflict(11, crowded)[1]
    assert bye_conflict(7, crowded) == (1.0, None)   # a quiet week is fine
    assert bye_conflict(None, crowded) == (1.0, None)
    assert bye_conflict(11, None) == (1.0, None)


def test_the_shortlist_never_hides_a_positions_best_player():
    """Regression: the best defence vanished behind two worse ones.

    Ranking by steepest drop is right for picking a position. Applied to a list
    of players it can surface a deeper name while the better one is never shown.
    """
    board = [
        val("Best DST", "DST", 21.1),
        val("Mid DST", "DST", 17.2),
        val("Worse DST", "DST", 16.5),
        val("A WR", "WR", 5.0),
    ]
    ranked = candidates(board, {"DST": 3.0, "WR": 0.5}, LEAGUE, {"WR": 0})
    shortlist = diversify(ranked, count=4, min_positions=2)
    names = [c.valuation.player.name for c in shortlist]
    assert "Best DST" in names
    assert names.index("Best DST") < names.index("Mid DST")
