"""Draft-day advice: what to take at this pick, and why.

Ties the pieces together. Projections give points and games; valuation turns
those into value above replacement; VONA turns value into a pick by asking
which position degrades most before your next turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.sources.projections import ProjectionSource
from nfl_fantasy.valuation import Valuation, value_board
from nfl_fantasy.vona import (
    Opportunity,
    blend_runs,
    missing_required,
    next_pick_after,
    opportunity_costs,
    runs_from_adp,
    runs_from_needs,
    snake_picks,
)

PROJECTION_DIR = Path("data/projections")


def normalize(name: str) -> str:
    """Loose key so 'Ja'Marr Chase' and 'JaMarr Chase' are the same player."""
    return "".join(c for c in name.lower() if c.isalnum())


@dataclass
class Advice:
    pick: int
    next_pick: int | None
    round_number: int
    opportunities: list[Opportunity]
    runs: dict[str, float]
    warnings: list[str]

    @property
    def recommendation(self) -> Valuation | None:
        return self.opportunities[0].best if self.opportunities else None


def load_players(key: str, directory: Path = PROJECTION_DIR) -> list[Player]:
    """Projections for a league, as players carrying points, games and ADP."""
    path = directory / f"{key}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No projections for {key!r} at {path}. Pull them from the platform first."
        )
    adp_path = directory / f"{key}_adp.csv"
    rankings = ProjectionSource(path, adp_path if adp_path.exists() else None).fetch(
        LeagueSettings(key=key, platform="yahoo", league_id="0")
    )
    return [
        Player(
            id=normalize(r.name),
            name=r.name,
            position=r.position,
            team=r.team,
            adp=r.adp,
            projected_points=r.projected_points,
            games=r.games,
            bye_week=r.bye_week,
        )
        for r in rankings
    ]


def advise(
    settings: LeagueSettings,
    players: list[Player],
    slot: int,
    taken: list[str],
    my_roster: list[str],
    rounds: int | None = None,
) -> Advice:
    """What to take now, given who is gone and what you already have."""
    rounds = rounds or len([s for s in settings.roster_slots if s != "IR"])
    board = value_board(settings, players)

    gone = {normalize(name) for name in taken}
    mine = {normalize(name) for name in my_roster}
    by_key = {v.player.id: v for v in board}

    available = [v for v in board if v.player.id not in gone]
    roster_counts: dict[str, int] = {}
    for key in mine:
        if key in by_key:
            position = by_key[key].player.position
            roster_counts[position] = roster_counts.get(position, 0) + 1

    picks = snake_picks(slot, settings.teams, rounds)

    # The window that matters runs from *your* turn to your next one, not from
    # wherever the draft happens to be. Asked between your turns, answer for the
    # turn you are about to get.
    board_pick = len(taken) + 1
    current = next((p for p in picks if p >= board_pick), board_pick)
    following = next_pick_after(current, slot, settings.teams, rounds)
    round_number = (current - 1) // settings.teams + 1

    # Two views of who goes next. ADP says where the market drafts a position;
    # roster need says which teams still have that hole to fill. Early rounds
    # are best-available so ADP leads; later, need does.
    adp = {v.player.name: v.player.adp for v in board if v.player.adp is not None}
    positions = {v.player.name: v.player.position for v in board}
    prior = runs_from_adp(adp, positions, current, following or current)

    taken_positions = [
        by_key[normalize(name)].player.position
        for name in taken
        if normalize(name) in by_key
    ]
    needs = runs_from_needs(
        settings, taken_positions, current, following or current, slot
    )
    runs = blend_runs(prior, needs, round_number)

    opportunities = opportunity_costs(available, runs, settings, roster_counts)
    if not opportunities and available:
        # Every position is at its depth cap, but the pick still has to be
        # spent. Fall back to best available rather than leaving a roster spot
        # empty -- an unused pick is strictly worse than a bench flyer.
        opportunities = opportunity_costs(available, runs, settings, roster_counts={})

    picks_left = sum(1 for p in picks if p >= current)
    warnings = []
    for position in missing_required(settings, roster_counts, picks_left):
        warnings.append(
            f"{position} slot still empty with only {picks_left} picks left -- "
            "every remaining pick is now forced."
        )

    return Advice(
        pick=current,
        next_pick=following,
        round_number=round_number,
        opportunities=opportunities,
        runs=runs,
        warnings=warnings,
    )
