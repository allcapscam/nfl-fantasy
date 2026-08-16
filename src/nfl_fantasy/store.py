"""On-disk cache of settings synced from the platforms."""

from __future__ import annotations

import json
from pathlib import Path

from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.sources.base import Ranking

SETTINGS_DIR = Path("data/settings")
RANKINGS_DIR = Path("data/rankings")


def save_settings(settings: LeagueSettings) -> Path:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    path = SETTINGS_DIR / f"{settings.key}.json"
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_settings(key: str) -> LeagueSettings:
    path = SETTINGS_DIR / f"{key}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No synced settings for {key!r}. Run: draftbot sync --league {key}"
        )
    return LeagueSettings.model_validate_json(path.read_text(encoding="utf-8"))


def save_rankings(key: str, rankings: list[Ranking]) -> Path:
    RANKINGS_DIR.mkdir(parents=True, exist_ok=True)
    path = RANKINGS_DIR / f"{key}.json"
    payload = [r.model_dump() for r in rankings]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_rankings(key: str) -> list[Ranking]:
    """Cached rankings for a league, or an empty list if none have been pulled."""
    path = RANKINGS_DIR / f"{key}.json"
    if not path.exists():
        return []
    return [Ranking.model_validate(r) for r in json.loads(path.read_text(encoding="utf-8"))]
