"""Upside: deliberately biasing toward players whose outcome is unknown.

This is a preference, not a correction. Projections are a median estimate, and
a median-optimal roster is not the same thing as a championship-optimal one --
in a ten-team league where four make the playoffs, the roster that wins is the
one with ceilings, not the one with the highest expected total.

Players with no prior-season production are where that ceiling lives. A rookie
receiver has no track record for a projection model to lean on, so his forecast
regresses hard toward the positional mean regardless of whether he is the next
star or a bust. The projection is not wrong; it is *uncertain*, and uncertainty
is worth more late in a draft than early.

The bonus therefore scales with the round. Round one is not the place to gamble
-- an established elite player is exactly what you want anchoring a roster --
but by the middle rounds you are choosing between similar medians, and the one
with an unknown ceiling is the better bet.

A caveat kept in the open: "no 2025 stats" catches two different players. True
unknowns like a rookie receiver, and established players who lost a season to
injury. Both carry real variance, so both get the bonus, but they are not the
same kind of bet and the advisor labels which is which where it can.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

#: Prior-season games at or below this count means there is little to project
#: from. A player who managed a handful of games is nearly as unknown as one
#: who managed none.
UNPROVEN_GAMES = 4

#: Ceiling premium at full strength. Applied to value above replacement, so a
#: quarter is a large but not board-overturning nudge.
MAX_UPSIDE_BONUS = 0.25

#: Rounds over which the bonus ramps in. Nothing in round one; full by here.
UPSIDE_FULL_BY_ROUND = 6.0


@dataclass(frozen=True)
class History:
    """What a player did last season, if anything."""

    games: int
    points: float

    @property
    def unproven(self) -> bool:
        return self.games <= UNPROVEN_GAMES


def load_history(path: str | Path) -> dict[str, History]:
    """Prior-season production keyed by normalized name."""
    from nfl_fantasy.advisor import normalize

    history: dict[str, History] = {}
    file = Path(path)
    if not file.exists():
        return history
    with file.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            try:
                games = int(float(row.get("prior_games") or 0))
                points = float(row.get("prior_points") or 0)
            except (TypeError, ValueError):
                continue
            history[normalize(name)] = History(games=games, points=points)
    return history


def upside_weight(round_number: int) -> float:
    """How much ceiling is worth relative to median, at this stage."""
    progress = max(0.0, round_number - 1) / (UPSIDE_FULL_BY_ROUND - 1)
    return min(1.0, progress) * MAX_UPSIDE_BONUS


def upside_multiplier(
    player_key: str, history: dict[str, History], round_number: int
) -> float:
    """Bonus for a player with no track record to project from.

    Players absent from the history file are treated as established: the file
    lists everyone with a thin or missing prior season, so silence means the
    player has a full one.
    """
    record = history.get(player_key)
    if record is None or not record.unproven:
        return 1.0
    return 1.0 + upside_weight(round_number)


def describe(player_key: str, history: dict[str, History]) -> str | None:
    """A short label for why a player is flagged, or None if he is not."""
    record = history.get(player_key)
    if record is None or not record.unproven:
        return None
    if record.games == 0:
        return "no 2025 games"
    return f"only {record.games} games in 2025"
