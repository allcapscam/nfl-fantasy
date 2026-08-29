"""Projections carrying points AND games, from one model.

The games column is what makes the per-game adjustment possible, and it has to
come from the same projection as the points. Mixing them -- points from one
provider, games from another -- silently corrupts the division: the points
already embed some games assumption, and dividing by a different number inflates
exactly the players the adjustment is meant to identify.

Yahoo's own player list satisfies this. It reports projected points computed
under the league's real scoring rules and projected games from the same Rotowire
model, so points/games is internally consistent and already reflects things like
six-point passing touchdowns.
"""

from __future__ import annotations

import csv
from pathlib import Path

from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.sources.base import Ranking

POSITION_MAP = {"DEF": "DST", "DST": "DST"}


def _number(value: str | None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


class ProjectionSource:
    """Reads a projections CSV: name, team, position, games, bye, points."""

    def __init__(self, path: str | Path, adp_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.adp_path = Path(adp_path) if adp_path else None

    def load_adp(self) -> dict[str, float]:
        """Average draft position by player name, if a file was supplied."""
        if not self.adp_path or not self.adp_path.exists():
            return {}
        adp: dict[str, float] = {}
        with self.adp_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                value = _number(row.get("adp"))
                name = (row.get("name") or "").strip()
                if name and value is not None:
                    adp[name] = value
        return adp

    def fetch(self, settings: LeagueSettings) -> list[Ranking]:
        adp = self.load_adp()
        rankings: list[Ranking] = []

        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                position = (row.get("position") or "").strip().upper()
                points = _number(row.get("points"))
                games = _number(row.get("games"))
                rankings.append(
                    Ranking(
                        name=name,
                        position=POSITION_MAP.get(position, position),
                        team=(row.get("team") or "").strip().upper() or None,
                        projected_points=points,
                        games=int(games) if games else None,
                        bye_week=int(b) if (b := _number(row.get("bye"))) else None,
                        adp=adp.get(name),
                    )
                )

        # Rank by points so anything downstream that wants an ordering has one.
        ordered = sorted(rankings, key=lambda r: r.projected_points or 0.0, reverse=True)
        for index, ranking in enumerate(ordered, start=1):
            ranking.rank = index
        return ordered
