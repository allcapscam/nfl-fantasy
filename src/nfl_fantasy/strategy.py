"""Strategy definition: the rules the bot drafts by.

The strategy lives in a YAML file so it can be edited between mocks without
touching code. `strategy.example.yaml` is the annotated starting point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Position = Literal["QB", "RB", "WR", "TE", "K", "DST"]


class League(BaseModel):
    """Settings that come from the league, not from you."""

    teams: int = 12
    draft_slot: int = Field(1, description="Your pick in round 1, 1-indexed.")
    scoring: Literal["standard", "half_ppr", "ppr"] = "half_ppr"
    roster: dict[str, int] = Field(
        default_factory=lambda: {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1, "BENCH": 6}
    )


class RoundPlan(BaseModel):
    """What you want to come away with in a given round."""

    round: int
    prefer: list[Position]
    avoid: list[Position] = Field(default_factory=list)


class Strategy(BaseModel):
    """The whole strategy: hard constraints plus soft preferences."""

    name: str = "default"
    league: League = Field(default_factory=League)

    # Hard constraints -- the bot will not violate these.
    earliest_round: dict[str, int] = Field(
        default_factory=dict,
        description="Position -> first round it may be taken. e.g. {'K': 14}",
    )
    max_per_position: dict[str, int] = Field(default_factory=dict)

    # Soft preferences -- used to rank players that pass the constraints.
    round_plan: list[RoundPlan] = Field(default_factory=list)
    position_weight: dict[str, float] = Field(
        default_factory=dict,
        description="Multiplier applied to a player's value by position.",
    )
    reach_tolerance: int = Field(
        8,
        description="How many ADP slots early the bot will take a player it wants.",
    )

    @classmethod
    def load(cls, path: str | Path) -> Strategy:
        """Read a strategy YAML file and validate it."""
        text = Path(path).read_text(encoding="utf-8")
        return cls.model_validate(yaml.safe_load(text) or {})

    def plan_for_round(self, round_number: int) -> RoundPlan | None:
        """The plan entry for a round, if one was written."""
        return next((p for p in self.round_plan if p.round == round_number), None)

    def may_draft(self, position: str, round_number: int, already_rostered: int) -> bool:
        """Check a position against the hard constraints only."""
        if round_number < self.earliest_round.get(position, 1):
            return False
        cap = self.max_per_position.get(position)
        return not (cap is not None and already_rostered >= cap)
