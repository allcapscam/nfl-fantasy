"""Platform adapters. One module per fantasy host (Sleeper, ESPN, Yahoo, NFL.com)."""

from nfl_fantasy.platforms.base import DraftPlatform, DraftState, Player

__all__ = ["DraftPlatform", "DraftState", "Player"]
