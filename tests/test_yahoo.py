"""Yahoo adapter tests against fixture payloads.

The live API needs app credentials, so these pin the parsing instead: Yahoo's
numeric-keyed collections, its list-of-fragments entities, and the slot and
scoring vocabularies. Those are where the adapter is most likely to be wrong.
"""

import pytest

from nfl_fantasy.platforms import yahoo as yahoo_module
from nfl_fantasy.platforms.yahoo import (
    SLOT_MAP,
    YahooAdapter,
    collection_items,
    merge_fragments,
)
from nfl_fantasy.platforms.yahoo_auth import YahooToken, authorize_url

# -- JSON shape helpers ------------------------------------------------------


def test_collection_items_skips_the_count_field():
    node = {"0": {"a": 1}, "1": {"a": 2}, "count": 2}
    assert list(collection_items(node)) == [{"a": 1}, {"a": 2}]


def test_collection_items_tolerates_a_non_dict():
    assert list(collection_items([])) == []


def test_merge_fragments_flattens_nested_lists():
    node = [[{"player_key": "461.p.1"}, {"name": {"full": "Ja'Marr Chase"}}], {"status": "A"}]
    merged = merge_fragments(node)
    assert merged["player_key"] == "461.p.1"
    assert merged["name"]["full"] == "Ja'Marr Chase"
    assert merged["status"] == "A"


def test_merge_fragments_keeps_the_first_non_empty_value():
    merged = merge_fragments([{"team": ""}, {"team": "CIN"}])
    assert merged["team"] == "CIN"


# -- fake transport ----------------------------------------------------------

LEAGUES_PAYLOAD = {
    "fantasy_content": {
        "users": {
            "0": {
                "user": [
                    {"guid": "ABC"},
                    {
                        "games": {
                            "0": {
                                "game": [
                                    {"game_key": "999", "code": "nfl"},
                                    {
                                        "leagues": {
                                            "0": {
                                                "league": [
                                                    {
                                                        "league_key": "999.l.6572",
                                                        "league_id": "6572",
                                                        "name": "League One",
                                                    }
                                                ]
                                            },
                                            "1": {
                                                "league": [
                                                    {
                                                        "league_key": "999.l.832043",
                                                        "league_id": "832043",
                                                        "name": "League Two",
                                                    }
                                                ]
                                            },
                                            "count": 2,
                                        }
                                    },
                                ]
                            },
                            "count": 1,
                        }
                    },
                ]
            },
            "count": 1,
        }
    }
}

STAT_CATEGORIES_PAYLOAD = {
    "fantasy_content": {
        "game": [
            {"game_key": "999"},
            {
                "stat_categories": {
                    "stats": [
                        {"stat": {"stat_id": 11, "name": "Receptions"}},
                        {"stat": {"stat_id": 5, "name": "Passing Touchdowns"}},
                    ]
                }
            },
        ]
    }
}

SETTINGS_PAYLOAD = {
    "fantasy_content": {
        "league": [
            {
                "league_key": "999.l.6572",
                "league_id": "6572",
                "name": "League One",
                "num_teams": 12,
            },
            {
                "settings": [
                    {
                        "roster_positions": [
                            {"roster_position": {"position": "QB", "count": 1}},
                            {"roster_position": {"position": "WR", "count": 3}},
                            {"roster_position": {"position": "RB", "count": 2}},
                            {"roster_position": {"position": "TE", "count": 1}},
                            {"roster_position": {"position": "W/R/T", "count": 1}},
                            {"roster_position": {"position": "K", "count": 1}},
                            {"roster_position": {"position": "DEF", "count": 1}},
                            {"roster_position": {"position": "BN", "count": 6}},
                        ],
                        "stat_modifiers": {
                            "stats": [
                                {"stat": {"stat_id": 11, "value": "0.5"}},
                                {"stat": {"stat_id": 5, "value": "4"}},
                            ]
                        },
                    }
                ]
            },
        ]
    }
}


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeClient:
    """Routes by URL fragment so one client serves the whole adapter."""

    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append(url)
        if "users;use_login=1" in url:
            return FakeResponse(LEAGUES_PAYLOAD)
        if "stat_categories" in url:
            return FakeResponse(STAT_CATEGORIES_PAYLOAD)
        if "/settings" in url:
            return FakeResponse(SETTINGS_PAYLOAD)
        raise AssertionError(f"unexpected url: {url}")


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(
        yahoo_module,
        "current_token",
        lambda client=None: YahooToken(
            access_token="tok", refresh_token="ref", expires_at=9e12
        ),
    )
    return YahooAdapter(key="yahoo1", league_id="6572", client=FakeClient())


# -- adapter behaviour -------------------------------------------------------


def test_resolves_league_key_from_the_numeric_id(adapter):
    """The URL id is not the API key; it has to be discovered per season."""
    assert adapter.resolve_league_key() == "999.l.6572"


def test_unknown_league_id_names_what_was_visible(monkeypatch):
    monkeypatch.setattr(
        yahoo_module,
        "current_token",
        lambda client=None: YahooToken(
            access_token="tok", refresh_token="ref", expires_at=9e12
        ),
    )
    wrong = YahooAdapter(key="x", league_id="1111", client=FakeClient())
    with pytest.raises(RuntimeError, match="999.l.6572"):
        wrong.resolve_league_key()


def test_fetch_settings_maps_slots_and_scoring(adapter):
    settings = adapter.fetch_settings()
    assert settings.platform == "yahoo"
    assert settings.teams == 12
    assert settings.name == "League One"
    # W/R/T is Yahoo's flex; DEF is our DST.
    assert settings.roster_slots.count("FLEX") == 1
    assert settings.roster_slots.count("DST") == 1
    assert settings.roster_slots.count("WR") == 3
    assert settings.bench_size == 6
    assert settings.scoring.format == "half_ppr"
    assert settings.scoring.pass_td == 4.0


def test_three_wr_plus_flex_raises_wr_demand(adapter):
    settings = adapter.fetch_settings()
    assert settings.max_startable("WR") == 4


def test_scoring_is_read_by_name_not_stat_id():
    """A wrong hardcoded stat id would mis-score a whole league silently."""
    scoring = YahooAdapter.parse_scoring(
        {"stat_modifiers": {"stats": [{"stat": {"stat_id": 11, "value": "1"}}]}},
        {"11": "Receptions"},
    )
    assert scoring.format == "ppr"

    # Same id, different meaning in the game's own categories -> not receptions.
    other = YahooAdapter.parse_scoring(
        {"stat_modifiers": {"stats": [{"stat": {"stat_id": 11, "value": "1"}}]}},
        {"11": "Rushing Yards"},
    )
    assert other.format == "standard"


def test_superflex_slot_is_recognised():
    assert SLOT_MAP["Q/W/R/T"] == "SUPER_FLEX"
    assert SLOT_MAP["W/R"] == "WR_RB_FLEX"


def test_parse_player_handles_multi_position_and_defense():
    player = YahooAdapter.parse_player(
        [
            [
                {"player_key": "999.p.1"},
                {"name": {"full": "Deebo Samuel Sr."}},
                {"editorial_team_abbr": "sf"},
                {"display_position": "WR,RB"},
            ]
        ]
    )
    assert player is not None
    assert player.position == "WR"  # first of the multi-position list
    assert player.team == "SF"

    defense = YahooAdapter.parse_player(
        [[{"player_key": "999.p.2"}, {"name": {"full": "Houston"}},
          {"display_position": "DEF"}, {"editorial_team_abbr": "hou"}]]
    )
    assert defense is not None and defense.position == "DST"


def test_parse_player_rejects_a_fragment_with_no_key():
    assert YahooAdapter.parse_player([{"name": {"full": "Nobody"}}]) is None


# -- auth --------------------------------------------------------------------


def test_authorize_url_uses_out_of_band_redirect():
    url = authorize_url("my-client-id")
    assert "redirect_uri=oob" in url
    assert "response_type=code" in url
    assert "client_id=my-client-id" in url


def test_token_expiry_has_a_margin():
    import time

    # Expires in 60s, but the margin is larger, so treat it as already expired
    # rather than start a draft call that dies mid-flight.
    assert YahooToken(
        access_token="a", refresh_token="b", expires_at=time.time() + 60
    ).expired
    assert not YahooToken(
        access_token="a", refresh_token="b", expires_at=time.time() + 3600
    ).expired
