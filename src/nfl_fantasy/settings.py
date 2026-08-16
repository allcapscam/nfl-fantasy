"""Normalized league settings.

Every platform describes rosters and scoring differently -- Sleeper uses a flat
list of slot strings, ESPN uses integer slot ids with counts, Yahoo uses its own
position tokens. Adapters translate into the models here so nothing downstream
has to care which platform a league lives on.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["sleeper", "espn", "yahoo"]

#: Positions a player can actually have.
REAL_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}

#: Slots that hold more than one position, and what each accepts.
FLEX_SLOTS: dict[str, set[str]] = {
    "FLEX": {"RB", "WR", "TE"},
    "WR_RB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}

#: Slots that don't hold a starter.
NON_STARTING_SLOTS = {"BN", "IR", "TAXI"}


def slot_accepts(slot: str, position: str) -> bool:
    """Can `position` be started in `slot`?"""
    if slot in FLEX_SLOTS:
        return position in FLEX_SLOTS[slot]
    return slot == position


class Scoring(BaseModel):
    """The handful of scoring rules that actually change draft strategy."""

    reception: float = 0.0
    te_reception_bonus: float = 0.0
    pass_td: float = 4.0

    @property
    def format(self) -> str:
        if self.reception >= 1.0:
            return "ppr"
        if self.reception > 0:
            return "half_ppr"
        return "standard"

    @property
    def is_te_premium(self) -> bool:
        return self.te_reception_bonus > 0


class LeagueSettings(BaseModel):
    """What a league is, as pulled from its platform."""

    key: str = Field(description="Your short name for this league, e.g. 'work'.")
    platform: Platform
    league_id: str
    name: str = ""
    teams: int = 12
    draft_slot: int | None = None
    draft_type: Literal["snake", "linear", "auction"] = "snake"
    roster_slots: list[str] = Field(default_factory=list)
    scoring: Scoring = Field(default_factory=Scoring)

    @property
    def starting_slots(self) -> list[str]:
        return [s for s in self.roster_slots if s not in NON_STARTING_SLOTS]

    @property
    def bench_size(self) -> int:
        return sum(1 for s in self.roster_slots if s == "BN")

    @property
    def is_superflex(self) -> bool:
        return "SUPER_FLEX" in self.roster_slots

    def slot_counts(self) -> dict[str, int]:
        return dict(Counter(self.starting_slots))

    def starters_at(self, position: str) -> int:
        """How many of `position` must start in a dedicated (non-flex) slot."""
        return sum(1 for s in self.starting_slots if s == position)

    def flex_slots_accepting(self, position: str) -> int:
        return sum(
            1 for s in self.starting_slots if s in FLEX_SLOTS and position in FLEX_SLOTS[s]
        )

    def max_startable(self, position: str) -> int:
        """Most of `position` that could be in a starting lineup at once."""
        return self.starters_at(position) + self.flex_slots_accepting(position)

    def describe(self) -> str:
        bits = [f"{self.teams}-team", self.scoring.format]
        if self.is_superflex:
            bits.append("superflex")
        if self.scoring.is_te_premium:
            bits.append("TE-premium")
        if self.draft_type != "snake":
            bits.append(self.draft_type)
        return " ".join(bits)
