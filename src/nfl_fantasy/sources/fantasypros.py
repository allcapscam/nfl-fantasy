"""FantasyPros expert consensus rankings.

Requires an API key in FANTASYPROS_API_KEY, sent as the x-api-key header --
requests without one return 403. Keys come from https://www.fantasypros.com/api-data/
(free prototype tier, or a production key with a HOF subscription).

If you don't have a key, use the CSV source instead: FantasyPros lets you export
the same rankings from the site, and `CsvRankingSource` reads that export.
"""

from __future__ import annotations

import os
import re

import httpx

from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.sources.base import Ranking

BASE_URL = "https://api.fantasypros.com/public/v2/json/nfl"

#: Our scoring formats -> FantasyPros scoring tokens.
SCORING_PARAM = {"standard": "STD", "half_ppr": "HALF", "ppr": "PPR"}

POSITION_MAP = {"DEF": "DST", "DST": "DST"}


def strip_position_rank(value: str) -> str:
    """FantasyPros writes positions as 'RB1', 'WR12'. We want 'RB'."""
    return re.sub(r"\d+$", "", (value or "").strip().upper())


class FantasyProsSource:
    """Consensus rankings, matched to a league's scoring and roster format."""

    def __init__(
        self,
        season: int,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.season = season
        self.api_key = api_key or os.environ.get("FANTASYPROS_API_KEY")
        self._client = client or httpx.Client(timeout=30.0)

    def fetch(self, settings: LeagueSettings) -> list[Ranking]:
        if not self.api_key:
            raise RuntimeError(
                "FANTASYPROS_API_KEY is not set. Get a key at "
                "https://www.fantasypros.com/api-data/, or export rankings to CSV "
                "and use: draftbot rankings --league <key> --csv <file>"
            )

        response = self._client.get(
            f"{BASE_URL}/{self.season}/consensus-rankings",
            headers={"x-api-key": self.api_key},
            params={
                "type": "draft",
                "scoring": SCORING_PARAM[settings.scoring.format],
                # 'OP' is FantasyPros' superflex/offensive-player board, which
                # ranks QBs against skill players instead of in their own list.
                "position": "OP" if settings.is_superflex else "ALL",
                "week": "0",
            },
        )
        if response.status_code == 403:
            raise RuntimeError(
                "FantasyPros rejected the API key (403). Check FANTASYPROS_API_KEY."
            )
        response.raise_for_status()
        return self.parse(response.json())

    @staticmethod
    def parse(payload: dict) -> list[Ranking]:
        """Pull rankings out of a consensus-rankings response."""
        rankings: list[Ranking] = []
        for record in payload.get("players", []):
            position = strip_position_rank(record.get("player_position_id", ""))
            rank = record.get("rank_ecr")
            rankings.append(
                Ranking(
                    name=record.get("player_name", "").strip(),
                    position=POSITION_MAP.get(position, position),
                    team=(record.get("player_team_id") or "").strip() or None,
                    rank=int(rank) if rank is not None else None,
                    # Expert consensus rank stands in for ADP: it is what the
                    # reach check needs -- roughly where a player goes.
                    adp=float(rank) if rank is not None else None,
                    tier=record.get("tier"),
                    bye_week=(
                        int(record["player_bye_week"])
                        if str(record.get("player_bye_week") or "").isdigit()
                        else None
                    ),
                )
            )
        return rankings
