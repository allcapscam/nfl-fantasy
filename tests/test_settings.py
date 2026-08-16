from nfl_fantasy.settings import LeagueSettings, Scoring, slot_accepts

STANDARD = LeagueSettings(
    key="std",
    platform="sleeper",
    league_id="1",
    roster_slots=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DST", "BN", "BN"],
)

SUPERFLEX = LeagueSettings(
    key="sf",
    platform="yahoo",
    league_id="2",
    roster_slots=["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "BN"],
    scoring=Scoring(reception=1.0, te_reception_bonus=0.5),
)


def test_slot_accepts():
    assert slot_accepts("FLEX", "RB")
    assert not slot_accepts("FLEX", "QB")
    assert slot_accepts("SUPER_FLEX", "QB")
    assert slot_accepts("RB", "RB")
    assert not slot_accepts("RB", "WR")


def test_bench_and_starters_split():
    assert STANDARD.bench_size == 2
    assert len(STANDARD.starting_slots) == 9


def test_max_startable_counts_flex():
    # Two dedicated RB slots plus the FLEX.
    assert STANDARD.max_startable("RB") == 3
    # QB has no flex home in a single-QB league.
    assert STANDARD.max_startable("QB") == 1


def test_superflex_changes_qb_demand():
    assert SUPERFLEX.is_superflex
    assert SUPERFLEX.max_startable("QB") == 2
    assert not STANDARD.is_superflex


def test_three_wr_league_wants_more_wr():
    assert SUPERFLEX.max_startable("WR") == 5  # three dedicated + FLEX + SUPER_FLEX
    assert STANDARD.max_startable("WR") == 3


def test_scoring_format_detection():
    assert STANDARD.scoring.format == "standard"
    assert SUPERFLEX.scoring.format == "ppr"
    assert SUPERFLEX.scoring.is_te_premium
    assert not STANDARD.scoring.is_te_premium


def test_describe():
    assert SUPERFLEX.describe() == "12-team ppr superflex TE-premium"
