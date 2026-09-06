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

from nfl_fantasy.settings import LeagueSettings

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
    qb_passing_td_premium: float = Field(
        0.06,
        description=(
            "QB value added per point of passing TD above the standard 4. "
            "At the default, a 6-point league lifts quarterbacks about 12%."
        ),
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

    def conflicts_with(self, settings: LeagueSettings) -> list[str]:
        """Ways this strategy fights the league it is pointed at.

        A strategy is portable, which is the point -- and also the risk. The
        rules here are hard constraints, so the engine obeys them however badly
        they fit: a QB gate written for a single-QB league silently survives
        being aimed at a superflex one, where two quarterbacks start and the
        position is the most valuable on the board. Nothing caught that, so it
        is caught here and reported rather than overridden. The strategy is
        yours; the warning is so the mismatch is a decision.
        """
        problems: list[str] = []

        if settings.is_superflex:
            starters = settings.max_startable("QB")
            gate = self.earliest_round.get("QB")
            if gate and gate > 2:
                problems.append(
                    f"QB is gated until round {gate}, but this league starts "
                    f"{starters} of them -- the top quarterbacks will be gone."
                )
            cap = self.max_per_position.get("QB")
            if cap is not None and cap < starters:
                problems.append(
                    f"max_per_position caps QB at {cap} in a league that starts "
                    f"{starters}; the lineup cannot be filled."
                )
            for plan in self.round_plan:
                if "QB" in plan.avoid and plan.round <= 3:
                    problems.append(
                        f"round {plan.round} avoids QB in a superflex league."
                    )

        for position in ("QB", "RB", "WR", "TE", "K", "DST"):
            required = settings.starters_at(position)
            cap = self.max_per_position.get(position)
            if required and cap is not None and cap < required:
                problems.append(
                    f"max_per_position caps {position} at {cap} but "
                    f"{required} must start."
                )
            gate = self.earliest_round.get(position)
            rounds = len(settings.starting_slots) + settings.bench_size
            if required and gate and gate > rounds:
                problems.append(
                    f"{position} is gated until round {gate}, past the "
                    f"{rounds}-round draft, but one has to start."
                )

        if settings.scoring.is_te_premium and self.earliest_round.get("TE", 1) > 4:
            problems.append(
                f"TE is gated until round {self.earliest_round['TE']} in a "
                "TE-premium league, where tight ends are lifted, not suppressed."
            )
        return problems
