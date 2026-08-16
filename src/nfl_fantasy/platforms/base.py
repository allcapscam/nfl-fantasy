"""The interface every platform adapter implements.

Keeping the draft logic behind this protocol means the strategy engine never
knows whether it is talking to Sleeper, ESPN, or a local mock draft.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class Player(BaseModel):
    id: str
    name: str
    position: str
    team: str | None = None
    adp: float | None = None
    projected_points: float | None = None
    bye_week: int | None = None


class DraftState(BaseModel):
    """A snapshot of the draft at the moment it is our turn."""

    round: int
    pick: int
    on_the_clock: bool
    my_roster: list[Player] = []
    drafted_player_ids: set[str] = set()


@runtime_checkable
class DraftPlatform(Protocol):
    """What an adapter has to be able to do."""

    def connect(self) -> None:
        """Authenticate and attach to the configured draft."""
        ...

    def get_state(self) -> DraftState:
        """Poll the current draft state."""
        ...

    def available_players(self) -> list[Player]:
        """Undrafted players, ideally with ADP and projections attached."""
        ...

    def make_pick(self, player: Player) -> bool:
        """Submit the pick. Returns True if the platform accepted it."""
        ...
