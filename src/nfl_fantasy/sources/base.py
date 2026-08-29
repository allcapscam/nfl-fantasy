"""Where rankings, ADP, and projections come from.

The platforms tell us who is available; they don't tell us who is good. Sleeper's
player endpoint carries neither ADP nor projections, so player value has to come
from a separate source. Anything implementing `RankingSource` can supply it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from nfl_fantasy.settings import LeagueSettings


class Ranking(BaseModel):
    """One player's value, as judged by a source."""

    name: str
    position: str
    team: str | None = None
    rank: int | None = None
    adp: float | None = None
    projected_points: float | None = None
    tier: int | None = None
    bye_week: int | None = None
    games: int | None = None


@runtime_checkable
class RankingSource(Protocol):
    def fetch(self, settings: LeagueSettings) -> list[Ranking]:
        """Rankings appropriate to this league's scoring and roster format."""
        ...
