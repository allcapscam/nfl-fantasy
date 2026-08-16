from nfl_fantasy.matching import apply_rankings, match_key, normalize_name
from nfl_fantasy.platforms.base import Player
from nfl_fantasy.sources.base import Ranking


def test_normalize_strips_suffixes_and_punctuation():
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("D.K. Metcalf") == "dk metcalf"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("Amon-Ra St. Brown") == "amonra st brown"
    assert normalize_name("Michael Pittman Jr.") == "michael pittman"


def test_normalize_handles_accents():
    assert normalize_name("San Francisco 49ers") == "san francisco ers"
    assert normalize_name("José Smith") == "jose smith"


def test_defenses_match_on_team_not_name():
    # A source calling it "Chiefs D/ST" and a platform calling it
    # "Kansas City Chiefs" still have to line up.
    assert match_key("Chiefs D/ST", "DST", "KC") == match_key(
        "Kansas City Chiefs", "DST", "KC"
    )


def test_defense_team_codes_do_not_collide():
    """Regression: KC and LAC once collapsed to the same key and shared a ranking."""
    codes = ["KC", "LAC", "LAR", "SF", "TB", "NO", "NE", "NYG", "NYJ", "GB"]
    keys = [match_key(f"{code} Defense", "DST", code) for code in codes]
    assert len(set(keys)) == len(codes)


def test_defense_falls_back_to_name_without_a_team_code():
    assert match_key("Chiefs D/ST", "DST", None) == "dst:CHIEFS"


def test_position_is_part_of_the_key():
    assert match_key("Josh Allen", "QB", "BUF") != match_key("Josh Allen", "LB", "JAX")


def test_apply_rankings_attaches_values():
    players = [Player(id="1", name="Kenneth Walker III", position="RB", team="SEA")]
    rankings = [
        Ranking(name="Kenneth Walker", position="RB", team="SEA", adp=24.0,
                projected_points=210.5, bye_week=10)
    ]
    enriched, unmatched = apply_rankings(players, rankings)
    assert not unmatched
    assert enriched[0].adp == 24.0
    assert enriched[0].projected_points == 210.5
    assert enriched[0].bye_week == 10


def test_unmatched_players_are_reported_not_dropped():
    players = [
        Player(id="1", name="Real Guy", position="WR", team="DAL"),
        Player(id="2", name="Nobody Knows Him", position="WR", team="NYJ"),
    ]
    rankings = [Ranking(name="Real Guy", position="WR", team="DAL", adp=5.0)]
    enriched, unmatched = apply_rankings(players, rankings)
    assert len(enriched) == 2  # nothing silently disappears from the board
    assert [p.name for p in unmatched] == ["Nobody Knows Him"]
