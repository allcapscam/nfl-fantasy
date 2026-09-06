"""Scoring a projected stat line under the league's own rules.

Sleeper ships precomputed totals, and using one means drafting under Sleeper's
idea of the format rather than the league's. Scoring the raw stat line instead
moves the risk from "is this the same format?" to "are these the right stat
keys?" -- which, unlike the first, can be checked against arithmetic the
platform has already done.
"""

from nfl_fantasy.leagues import LeagueRegistry
from nfl_fantasy.scoring import (
    SLEEPER_HALF_PPR,
    compare_tables,
    reconcile,
    reconcile_by_position,
    score_line,
    unused_keys,
)

# A quarterback's projected season: 4,200 yards, 30 TDs, 10 picks, some rushing.
QB_LINE = {
    "pass_yd": 4200, "pass_td": 30, "pass_int": 10,
    "rush_yd": 300, "rush_td": 3, "fum_lost": 4,
    "gp": 16,
}
# 4200*.04 + 30*4 + 10*-1 + 300*.1 + 3*6 + 4*-2 = 168 + 120 - 10 + 30 + 18 - 8
QB_HALF_PPR = 318.0

WR_LINE = {"rec": 95, "rec_yd": 1250, "rec_td": 9, "rush_yd": 40, "fum_lost": 1, "gp": 17}
# 95*.5 + 1250*.1 + 9*6 + 40*.1 + 1*-2 = 47.5 + 125 + 54 + 4 - 2
WR_HALF_PPR = 228.5


def test_a_stat_line_scores_to_the_hand_computed_total():
    assert score_line(QB_LINE, SLEEPER_HALF_PPR) == QB_HALF_PPR
    assert score_line(WR_LINE, SLEEPER_HALF_PPR) == WR_HALF_PPR


def test_rules_the_league_does_not_have_contribute_nothing():
    """A stat the table does not name is ignored, not guessed at."""
    no_ppr = {k: v for k, v in SLEEPER_HALF_PPR.items() if k != "rec"}
    assert score_line(WR_LINE, no_ppr) == WR_HALF_PPR - 95 * 0.5


def test_missing_and_unparseable_stats_are_skipped_not_fatal():
    line = {"pass_yd": None, "pass_td": "", "rush_yd": "not a number", "rec_td": 2}
    assert score_line(line, SLEEPER_HALF_PPR) == 12.0


def test_reconcile_accepts_a_table_that_reproduces_the_platform_total():
    lines = [
        ("QB One", {**QB_LINE, "pts_half_ppr": QB_HALF_PPR}),
        ("WR One", {**WR_LINE, "pts_half_ppr": WR_HALF_PPR}),
    ]
    check = reconcile(lines)
    assert check.checked == 2
    assert check.agreement == 1.0
    assert check.ok()


def test_reconcile_rejects_a_table_whose_stat_keys_are_wrong():
    """The failure this exists to catch: a renamed key pays out nothing.

    Every total is still a plausible number, just smaller -- which is exactly
    the kind of error that survives being looked at.
    """
    renamed = {("passing_yards" if k == "pass_yd" else k): v
               for k, v in SLEEPER_HALF_PPR.items()}
    lines = [("QB One", {**QB_LINE, "pts_half_ppr": QB_HALF_PPR})]
    check = reconcile(lines, table=renamed)
    assert not check.ok()
    assert "passing_yards" in check.missing_keys


def test_a_key_no_player_has_is_reported_but_does_not_fail_the_check():
    """Sleeper does not project every rule it scores.

    A missed-field-goal rule that never pays out is worth saying out loud, but
    refusing a whole board over it would be wrong.
    """
    lines = [("QB One", {**QB_LINE, "pts_half_ppr": QB_HALF_PPR}),
             ("WR One", {**WR_LINE, "pts_half_ppr": WR_HALF_PPR})]
    check = reconcile(lines)
    assert check.ok()                       # agreement is what decides
    assert "fgm_50p" in check.missing_keys  # and this is only reported

    # A receiver has receptions, so that rule pays out; nobody kicks.
    assert unused_keys(lines, {"rec": 0.5, "fgm_50p": 5}) == ["fgm_50p"]


def test_compare_tables_shows_where_two_scorings_actually_differ():
    harsher = {**SLEEPER_HALF_PPR, "fum_lost": -4.0}
    lines = [("QB One", "QB", QB_LINE), ("WR One", "WR", WR_LINE)]
    diffs = compare_tables(lines, harsher)
    assert diffs["QB"][1] == -8.0    # four fumbles, two extra points each
    assert diffs["WR"][1] == -2.0


def test_the_league_scoring_matches_sleeper_except_on_kicker_misses():
    """The question this was built to answer, pinned.

    Sleeper's half-PPR and this league agree on every rule that touches a
    quarterback, back, receiver, tight end or defence. They part only on missed
    field goals, which Yahoo splits by distance and penalises harder -- and a
    kicker is a last-round pick whose spread above replacement is negligible.
    If that ever stops being true, this test says so.
    """
    ours = LeagueRegistry.load("leagues.example.yaml").get("money").manual.scoring_table
    assert ours, "the example registry should carry a worked scoring_table"
    differing = {k for k in set(ours) | set(SLEEPER_HALF_PPR)
                 if ours.get(k) != SLEEPER_HALF_PPR.get(k)}
    assert all(k.startswith("fgmiss") for k in differing), differing


def test_a_line_with_no_scorable_stat_is_skipped_not_counted_wrong():
    """Sleeper files the odd linebacker under K: tackles, and no kicks.

    Scoring him yields zero against a real total, so counted as a mismatch he
    is evidence the stat keys are wrong -- which he is not. He is evidence the
    feed put a defender in the kicker list. One such record used to fail a
    46-player position outright.
    """
    kicker = {"xpm": 40, "fgm_40_49": 10, "gp": 17}
    kicker_total = 40 * 1.0 + 10 * 4.0
    misfiled = {"idp_tkl": 36, "idp_sack": 1, "gp": 18}
    lines = [("Real Kicker", {**kicker, "pts_half_ppr": kicker_total}),
             ("Misfiled LB", {**misfiled, "pts_half_ppr": 3.0})]

    check = reconcile(lines)
    assert check.checked == 1          # only the one there was anything to check
    assert check.unscoreable == 1
    assert check.agreement == 1.0
    assert check.ok()


def test_a_wholesale_naming_error_still_fails_even_though_lines_are_skipped():
    """Skipping unscorable lines must not become a way to pass by scoring none.

    Rename every key and every line is unscorable -- so `checked` is zero, and
    a check that verified nothing is not a check that passed.
    """
    nonsense = {f"x_{k}": v for k, v in SLEEPER_HALF_PPR.items()}
    lines = [("QB One", {**QB_LINE, "pts_half_ppr": QB_HALF_PPR}),
             ("WR One", {**WR_LINE, "pts_half_ppr": WR_HALF_PPR})]

    check = reconcile(lines, table=nonsense)
    assert check.checked == 0
    assert check.unscoreable == 2
    assert not check.ok()


def test_positions_are_reconciled_separately_so_one_bad_feed_costs_only_itself():
    """The failure that blocked a real pull.

    Sleeper's projected line for a defence carries sacks, interceptions,
    fumble recoveries and blocked kicks -- and no points-allowed bucket, which
    is most of a defence's score. So nothing reproduces its defensive total,
    while every skill player reconciles exactly. Over the whole board that is
    95% agreement and a refusal that leaves no board at all; per position it is
    the skill positions proven and the defences named as unsupported.
    """
    defence = {"sack": 45, "int": 14, "fum_rec": 8, "blk_kick": 1, "gp": 17}
    from_stats = 45 * 1.0 + 14 * 2.0 + 8 * 2.0 + 1 * 2.0
    lines = [("QB One", "QB", {**QB_LINE, "pts_half_ppr": QB_HALF_PPR}),
             ("WR One", "WR", {**WR_LINE, "pts_half_ppr": WR_HALF_PPR}),
             # Sleeper's own number for a defence is not its stat line's worth.
             ("SEA", "DST", {**defence, "pts_half_ppr": from_stats - 10})]

    checks = reconcile_by_position(lines)
    assert checks["QB"].ok() and checks["WR"].ok()
    assert not checks["DST"].ok()
    assert checks["DST"].worst_error == 10.0
