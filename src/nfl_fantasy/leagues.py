"""Multi-league configuration.

You play in several leagues on several platforms. `leagues.yaml` is the registry:
each entry names a platform, the league's id on that platform, and the strategy
file to draft it with. Roster and scoring rules are NOT listed here -- those are
pulled from the platform by `draftbot sync`, so they can't drift out of date.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from nfl_fantasy.settings import Platform


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
