"""FantasyPros expert consensus rankings and projections.

Requires an API key in FANTASYPROS_API_KEY, sent as the x-api-key header.
Keys come from https://www.fantasypros.com/api-data/.

A warning about key tiers, learned the hard way. Free-tier keys return
`public_api_limited: true` and only the first 10 players, no matter what limit,
offset, or page you pass. Ten players cannot drive a sixteen-round draft, so
`fetch` refuses to return a board that short rather than let the bot draft off
it. Use the CSV export path instead, or a production key.

A second trap: position-filtered requests renumber `rank_ecr` from 1, so the
QB1, the K1, and the RB1 all come back as rank 1. Ranks are only comparable
across positions when the request is unfiltered (position=ALL or OP), which is
why this module never merges per-position ranking calls into one board.
Projections are different -- those are absolute points, so merging them across
positions is safe, and `fetch_projections` does exactly that.
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

#: Which projection field to read for each scoring format.
POINTS_FIELD = {"standard": "points", "half_ppr": "points_half", "ppr": "points_ppr"}

#: Positions to pull projections for. Safe to merge: points are absolute.
PROJECTION_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")

POSITION_MAP = {"DEF": "DST", "DST": "DST"}

#: Below this, a board is too short to draft from and we raise instead.
MINIMUM_BOARD = 100


class FreeTierError(RuntimeError):
    """The key is real but capped too low to be usable."""


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
        minimum_board: int = MINIMUM_BOARD,
    ) -> None:
        self.season = season
        self.api_key = api_key or os.environ.get("FANTASYPROS_API_KEY")
        self.minimum_board = minimum_board
        self._client = client or httpx.Client(timeout=30.0)

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "FANTASYPROS_API_KEY is not set. Get a key at "
                "https://www.fantasypros.com/api-data/, or export rankings to CSV "
                "and use: draftbot rankings --league <key> --csv <file>"
            )
        response = self._client.get(
            f"{BASE_URL}/{self.season}/{path}",
            headers={"x-api-key": self.api_key},
            params=params,
        )
        if response.status_code == 403:
            raise RuntimeError(
                "FantasyPros rejected the API key (403). Check FANTASYPROS_API_KEY."
            )
        response.raise_for_status()
        return response.json()

    def fetch(self, settings: LeagueSettings) -> list[Ranking]:
        payload = self._get(
            "consensus-rankings",
            {
                "type": "draft",
                "scoring": SCORING_PARAM[settings.scoring.format],
                # 'OP' is the superflex board, ranking QBs against skill players.
                # Both ALL and OP return true overall ranks; per-position calls
                # do not, so they are never used to build a board.
                "position": "OP" if settings.is_superflex else "ALL",
                "week": "0",
            },
        )
        rankings = self.parse(payload)

        if payload.get("public_api_limited") and len(rankings) < self.minimum_board:
            raise FreeTierError(
                f"FantasyPros returned only {len(rankings)} players "
                f"(tier: {payload.get('tier', 'unknown')}, of "
                f"{payload.get('count')} available). A free-tier key is capped at "
                "10 and cannot fill a draft board.\n"
                "Either upgrade to a production key (HOF subscription), or export "
                "rankings from the FantasyPros site as CSV and run:\n"
                "  draftbot rankings --league <key> --csv <file.csv>"
            )

        projections = self.fetch_projections(settings)
        return self.merge_projections(rankings, projections)

    def fetch_projections(self, settings: LeagueSettings) -> dict[str, float]:
        """Projected points keyed by player name.

        Merging across positions is valid here: unlike ranks, projected points
        are an absolute scale.
        """
        field = POINTS_FIELD[settings.scoring.format]
        points: dict[str, float] = {}
        for position in PROJECTION_POSITIONS:
            try:
                payload = self._get(
                    "projections",
                    {"position": position, "week": "0",
                     "scoring": SCORING_PARAM[settings.scoring.format]},
                )
            except httpx.HTTPError:
                continue  # a missing position shouldn't sink the whole pull
            for record in payload.get("players", []):
                name = (record.get("name") or "").strip()
                stats = record.get("stats") or {}
                value = stats.get(field, stats.get("points"))
                if name and value is not None:
                    points[name] = float(value)
        return points

    @staticmethod
    def merge_projections(
        rankings: list[Ranking], projections: dict[str, float]
    ) -> list[Ranking]:
        for ranking in rankings:
            if ranking.projected_points is None:
                ranking.projected_points = projections.get(ranking.name)
        return rankings

    @staticmethod
    def parse(payload: dict) -> list[Ranking]:
        """Pull rankings out of a consensus-rankings response."""
        rankings: list[Ranking] = []
        for record in payload.get("players", []):
            position = strip_position_rank(record.get("player_position_id", ""))
            rank = record.get("rank_ecr")
            rankings.append(
                Ranking(
                    name=(record.get("player_name") or "").strip(),
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
