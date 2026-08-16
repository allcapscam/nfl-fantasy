import pytest

from nfl_fantasy.settings import LeagueSettings, Scoring
from nfl_fantasy.sources.csv_source import CsvRankingSource
from nfl_fantasy.sources.fantasypros import (
    SCORING_PARAM,
    FantasyProsSource,
    strip_position_rank,
)

LEAGUE = LeagueSettings(key="t", platform="sleeper", league_id="1")


def test_strip_position_rank():
    assert strip_position_rank("RB1") == "RB"
    assert strip_position_rank("WR12") == "WR"
    assert strip_position_rank("DST") == "DST"


def test_scoring_param_covers_every_format():
    for fmt in ("standard", "half_ppr", "ppr"):
        assert fmt in SCORING_PARAM


def test_fantasypros_parses_a_response():
    payload = {
        "players": [
            {
                "player_name": "Ja'Marr Chase",
                "player_position_id": "WR1",
                "player_team_id": "CIN",
                "rank_ecr": 1,
                "tier": 1,
                "player_bye_week": "10",
            },
            {
                "player_name": "Chiefs",
                "player_position_id": "DST",
                "player_team_id": "KC",
                "rank_ecr": 140,
                "tier": 9,
                "player_bye_week": "",
            },
        ]
    }
    rankings = FantasyProsSource.parse(payload)
    assert rankings[0].position == "WR"
    assert rankings[0].adp == 1.0  # ECR stands in for ADP
    assert rankings[0].bye_week == 10
    assert rankings[1].position == "DST"
    assert rankings[1].bye_week is None  # empty bye must not crash the parse


def test_fantasypros_without_a_key_explains_the_alternative():
    source = FantasyProsSource(season=2026, api_key=None)
    with pytest.raises(RuntimeError, match="FANTASYPROS_API_KEY"):
        source.fetch(LEAGUE)


def test_superflex_requests_the_op_board(monkeypatch):
    """Superflex leagues need QBs ranked against skill players, not separately."""
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"players": []}

    class FakeClient:
        def get(self, url, headers, params):
            captured.update(params)
            return FakeResponse()

    superflex = LEAGUE.model_copy(
        update={"roster_slots": ["QB", "SUPER_FLEX"], "scoring": Scoring(reception=1.0)}
    )
    FantasyProsSource(season=2026, api_key="x", client=FakeClient()).fetch(superflex)
    assert captured["position"] == "OP"
    assert captured["scoring"] == "PPR"


def test_csv_source_reads_a_fantasypros_style_export(tmp_path):
    path = tmp_path / "rankings.csv"
    path.write_text(
        '"RK","TIERS","PLAYER NAME","TEAM","POS","BYE WEEK"\n'
        '"1","1","Ja\'Marr Chase","CIN","WR1","10"\n'
        '"2","1","Bijan Robinson","ATL","RB1","5"\n',
        encoding="utf-8",
    )
    rankings = CsvRankingSource(path).fetch(LEAGUE)
    assert [r.name for r in rankings] == ["Ja'Marr Chase", "Bijan Robinson"]
    assert rankings[0].position == "WR"
    assert rankings[0].tier == 1
    assert rankings[0].adp == 1.0  # rank fills in for a missing ADP column
    assert rankings[1].bye_week == 5


def test_csv_source_rejects_a_file_with_no_name_column(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="player-name column"):
        CsvRankingSource(path).fetch(LEAGUE)
