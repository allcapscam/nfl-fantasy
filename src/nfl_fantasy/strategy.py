"""Strategy definition: the rules the bot drafts by.

A strategy is portable across leagues on purpose. It says how you like to draft
-- not how many WRs start, which is a property of the league and gets pulled
from the platform. That split means one strategy file can be pointed at several
leagues, and the engine adapts it to each league's roster rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Position = Literal["QB", "RB", "WR", "TE", "K", "DST"]


class RoundPlan(BaseModel):
    """What you want to come away with in a given round."""

    round: int
    prefer: list[Position] = Field(default_factory=list)
    avoid: list[Position] = Field(default_factory=list)


class Strategy(BaseModel):
    """Hard constraints plus soft preferences."""

    name: str = "default"

    # Hard constraints -- never violated.
    earliest_round: dict[str, int] = Field(
        default_factory=dict,
        description="Position -> first round it may be taken, e.g. {'K': 14}",
    )
    max_per_position: dict[str, int] = Field(default_factory=dict)

    # Soft preferences -- rank the players that pass the constraints.
    round_plan: list[RoundPlan] = Field(default_factory=list)
    position_weight: dict[str, float] = Field(default_factory=dict)
    reach_tolerance: int = Field(
        8, description="How many ADP slots early the bot will take a player it wants."
    )

    # Format adjustments, applied only when the synced league settings match.
    superflex_qb_weight: float = Field(
        1.35, description="QB multiplier when the league has a superflex slot."
    )
    te_premium_weight: float = Field(
        1.15, description="TE multiplier when the league gives TEs bonus PPR."
    )

    @classmethod
    def load(cls, path: str | Path) -> Strategy:
        text = Path(path).read_text(encoding="utf-8")
        return cls.model_validate(yaml.safe_load(text) or {})

    def plan_for_round(self, round_number: int) -> RoundPlan | None:
        return next((p for p in self.round_plan if p.round == round_number), None)

    def may_draft(self, position: str, round_number: int, already_rostered: int) -> bool:
        """Hard constraints only."""
        if round_number < self.earliest_round.get(position, 1):
            return False
        cap = self.max_per_position.get(position)
        return not (cap is not None and already_rostered >= cap)
