"""Multi-league configuration.

You play in several leagues on several platforms. `leagues.yaml` is the registry:
each entry names a platform, the league's id on that platform, and the strategy
file to draft it with. Roster and scoring rules are NOT listed here -- those are
pulled from the platform by `draftbot sync`, so they can't drift out of date.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from nfl_fantasy.settings import LeagueSettings, Platform, Scoring


class ManualSettings(BaseModel):
    """League rules typed by hand, for when the platform can't be read.

    Syncing from the platform is always preferred -- it can't drift. But Yahoo
    gates its API behind manual approval and ESPN has no adapter yet, and a
    queue only really needs the roster shape and the scoring format. This lets
    those leagues work in the meantime.
    """

    teams: int = 12
    roster: list[str] = Field(
        description="Starting and bench slots, e.g. [QB, RB, RB, WR, WR, TE, FLEX, K, DST, BN, BN]"
    )
    scoring: Literal["standard", "half_ppr", "ppr"] = "half_ppr"
    te_premium: float = Field(0.0, description="Bonus points per TE reception.")
    name: str = ""

    def to_settings(self, key: str, platform: Platform, league_id: str) -> LeagueSettings:
        reception = {"standard": 0.0, "half_ppr": 0.5, "ppr": 1.0}[self.scoring]
        return LeagueSettings(
            key=key,
            platform=platform,
            league_id=league_id,
            name=self.name,
            teams=self.teams,
            roster_slots=[slot.strip().upper() for slot in self.roster],
            scoring=Scoring(reception=reception, te_reception_bonus=self.te_premium),
        )


class LeagueRef(BaseModel):
    """One league: where it lives and how to draft it."""

    key: str
    platform: Platform
    league_id: str
    draft_id: str | None = Field(
        None, description="Optional. Discovered from the league if omitted."
    )
    strategy: Path = Field(description="Path to this league's strategy YAML.")
    enabled: bool = True
    manual: ManualSettings | None = Field(
        None, description="Hand-entered rules used when the platform can't be read."
    )


class LeagueRegistry(BaseModel):
    leagues: list[LeagueRef] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> LeagueRegistry:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        entries = raw.get("leagues", {})
        # Accept either a mapping keyed by short name or an explicit list.
        if isinstance(entries, dict):
            entries = [{"key": key, **value} for key, value in entries.items()]
        return cls.model_validate({"leagues": entries})

    def get(self, key: str) -> LeagueRef:
        for league in self.leagues:
            if league.key == key:
                return league
        known = ", ".join(sorted(x.key for x in self.leagues)) or "none configured"
        raise KeyError(f"No league named {key!r}. Known leagues: {known}")

    @property
    def active(self) -> list[LeagueRef]:
        return [x for x in self.leagues if x.enabled]

    def by_platform(self, platform: Platform) -> list[LeagueRef]:
        return [x for x in self.active if x.platform == platform]
