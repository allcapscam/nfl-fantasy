"""The interface every platform adapter implements.

Keeping draft logic behind this protocol means the engine never knows whether
it is talking to Sleeper, ESPN, Yahoo, or a local mock.

Note on writes: none of the three platforms expose a supported endpoint for
submitting a draft pick, so `make_pick` is intentionally absent. Adapters read
state and export rankings; the pick itself is made by you or by the platform's
own autodraft running off the queue this tool produces.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from nfl_fantasy.settings import LeagueSettings


class Player(BaseModel):
    id: str
    name: str
    position: str
    team: str | None = None
    adp: float | None = None
    projected_points: float | None = None
    bye_week: int | None = None
    #: Projected games played. Distinct from total points: a player who
    #: misses time is not worse per week, only available less often.
    games: int | None = None


class DraftState(BaseModel):
    """A snapshot of the draft at a moment in time."""

    round: int
    pick: int
    on_the_clock: bool = False
    my_roster: list[Player] = []
    drafted_player_ids: set[str] = set()


@runtime_checkable
class DraftPlatform(Protocol):
    """What an adapter has to be able to do."""

    def fetch_settings(self) -> LeagueSettings:
        """Pull roster slots and scoring rules from the platform."""
        ...

    def get_state(self) -> DraftState:
        """Poll the current draft state."""
        ...

    def available_players(self) -> list[Player]:
        """Undrafted players, with ADP and projections where available."""
        ...
