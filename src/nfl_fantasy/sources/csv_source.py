"""Rankings from a CSV export.

This is the no-API-key path: FantasyPros (and most ranking sites) let you
download rankings as CSV. Column names vary between exports, so headers are
matched loosely rather than assumed.
"""

from __future__ import annotations

import csv
from pathlib import Path

from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.sources.base import Ranking
from nfl_fantasy.sources.fantasypros import strip_position_rank

#: Candidate header names, in priority order, for each field we want.
COLUMNS = {
    "name": ["player name", "player", "name", "playername"],
    "team": ["team", "tm", "player team"],
    "position": ["pos", "position", "player position"],
    "rank": ["rk", "rank", "overall rank", "ecr"],
    "adp": ["adp", "avg pick", "average draft position"],
    "projected_points": ["fpts", "proj pts", "projected points", "points"],
    "tier": ["tiers", "tier"],
    "bye_week": ["bye week", "bye"],
}


def _pick_column(header: list[str], candidates: list[str]) -> str | None:
    lowered = {h.strip().lower(): h for h in header}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _to_float(value: str | None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value: str | None) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


class CsvRankingSource:
    """Reads a rankings CSV. Ignores `settings` -- export the right file yourself."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self, settings: LeagueSettings) -> list[Ranking]:
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            mapping = {
                field: _pick_column(header, candidates)
                for field, candidates in COLUMNS.items()
            }
            if not mapping["name"]:
                raise ValueError(
                    f"{self.path} has no recognizable player-name column. "
                    f"Found: {', '.join(header)}"
                )

            rankings = []
            for row in reader:
                name = (row.get(mapping["name"]) or "").strip()
                if not name:
                    continue
                raw_position = row.get(mapping["position"]) if mapping["position"] else ""
                rankings.append(
                    Ranking(
                        name=name,
                        position=strip_position_rank(raw_position or ""),
                        team=(row.get(mapping["team"]) or "").strip() or None
                        if mapping["team"]
                        else None,
                        rank=_to_int(row.get(mapping["rank"])) if mapping["rank"] else None,
                        adp=_to_float(row.get(mapping["adp"])) if mapping["adp"] else None,
                        projected_points=(
                            _to_float(row.get(mapping["projected_points"]))
                            if mapping["projected_points"]
                            else None
                        ),
                        tier=_to_int(row.get(mapping["tier"])) if mapping["tier"] else None,
                        bye_week=(
                            _to_int(row.get(mapping["bye_week"]))
                            if mapping["bye_week"]
                            else None
                        ),
                    )
                )
        # An export with ranks but no ADP still needs something for the reach
        # check; overall rank is the best available stand-in.
        for ranking in rankings:
            if ranking.adp is None and ranking.rank is not None:
                ranking.adp = float(ranking.rank)
        return rankings
