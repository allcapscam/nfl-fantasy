"""Stale projections, manual news flags, and side-car byes."""

from nfl_fantasy.flags import (
    SUSPICIOUS_GAP,
    Flag,
    disagreement_note,
    load_byes,
    load_flags,
    market_disagreement,
)


def test_a_big_gap_between_value_and_market_is_flagged():
    """The Josh Jacobs case.

    The model rated him the best value on the board while he sat on the
    commissioner's exempt list. The feed was days stale; the market was not.
    A gap that large is more often missing news than free value.
    """
    # Value rank 33, drafted at 63 -- thirty picks of disagreement.
    assert market_disagreement(33, 63.0) == 30
    assert "check for news" in disagreement_note(market_disagreement(33, 63.0))


def test_small_gaps_are_left_alone():
    assert market_disagreement(40, 45.0) is None       # ordinary noise
    assert market_disagreement(40, 40.0 + SUSPICIOUS_GAP) is not None
    assert disagreement_note(None) is None


def test_a_player_the_market_likes_more_is_not_flagged():
    """Only the model-likes-him-more direction is suspicious."""
    assert market_disagreement(80, 20.0) is None


def test_no_adp_means_no_signal():
    assert market_disagreement(10, None) is None


def test_flags_can_remove_a_player_entirely(tmp_path):
    path = tmp_path / "flags.csv"
    path.write_text(
        "name,action,reason\n"
        "Josh Jacobs,exclude,on the commissioner's exempt list\n"
        "Someone Else,downgrade,limited in practice\n",
        encoding="utf-8",
    )
    flags = load_flags(path)
    assert flags["joshjacobs"].excluded
    assert "exempt" in flags["joshjacobs"].reason
    assert not flags["someoneelse"].excluded
    assert load_flags(tmp_path / "missing.csv") == {}


def test_byes_load_from_a_side_car(tmp_path):
    """ESPN's projections carry no byes; Yahoo's do. A file covers the gap."""
    path = tmp_path / "byes.csv"
    path.write_text("name,bye\nJosh Allen,7\nBreece Hall,13\nBroken,\n", encoding="utf-8")
    byes = load_byes(path)
    assert byes["joshallen"] == 7
    assert byes["breecehall"] == 13
    assert "broken" not in byes          # a blank week is not a bye
    assert load_byes(tmp_path / "missing.csv") == {}


def test_flag_defaults_to_downgrade():
    assert not Flag("downgrade", "").excluded
