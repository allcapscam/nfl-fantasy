"""On-disk cache of settings synced from the platforms."""

from __future__ import annotations

from pathlib import Path

from nfl_fantasy.settings import LeagueSettings

SETTINGS_DIR = Path("data/settings")


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
