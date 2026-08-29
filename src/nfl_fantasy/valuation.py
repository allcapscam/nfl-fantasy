"""Player value: games-adjusted points, measured above replacement.

Two ideas drive this, and both correct mistakes that raw projections invite.

Total projected points quietly punishes a player who misses time. A quarterback
projected for 7 games is not a low-end QB2 -- he is a starter you have to cover
for. What a missed game actually costs you is not his production, it is the
*difference* between his production and whoever you stream in his place. So
value is his own points plus replacement-level production for the weeks he is
out.

And points alone are not comparable across positions. In a ten-team league with
one starting quarterback, the QB11 is free; the RB25 is not. Value is therefore
measured above the last player who would realistically start at that position,
counting the flex slot's demand.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import FLEX_SLOTS, LeagueSettings

#: Games in a fantasy regular season plus playoffs, less the bye. Yahoo's own
#: projected-games column tops out here, so the two agree.
SEASON_GAMES = 16

#: Positions a flex slot can absorb, in the leagues we handle.
FLEXIBLE = ("RB", "WR", "TE")


@dataclass(frozen=True)
class Valuation:
    """What a player is worth, and the pieces that make it up."""

    player: Player
    points: float
    games: int | None
    adjusted: float
    replacement: float

    @property
    def vor(self) -> float:
        """Value over replacement -- the number to compare across positions."""
        return self.adjusted - self.replacement

    @property
    def ppg(self) -> float | None:
        if not self.games:
            return None
        return self.points / self.games


def points_per_game(player: Player) -> float | None:
    if player.projected_points is None or not player.games:
        return None
    return player.projected_points / player.games


def adjusted_points(player: Player, replacement_ppg: float) -> float:
    """Season value once missed games are backfilled at replacement level.

    A player with no games figure (defenses) is taken at face value.
    """
    if player.projected_points is None:
        return 0.0
    if not player.games or player.games >= SEASON_GAMES:
        return player.projected_points
    missed = SEASON_GAMES - player.games
    return player.projected_points + replacement_ppg * missed


def dedicated_starters(settings: LeagueSettings) -> dict[str, int]:
    """Starting slots per position that only that position can fill."""
    return {
        position: settings.starters_at(position) * settings.teams
        for position in ("QB", "RB", "WR", "TE", "K", "DST")
    }


def allocate_flex(
    settings: LeagueSettings, by_position: dict[str, list[Player]], counts: dict[str, int]
) -> dict[str, int]:
    """Hand each flex slot to whichever position has the better next player.

    A fixed split (say 60/40 RB/WR) would be a guess. Assigning greedily by the
    value actually on the board lets the league's own player pool decide how
    much of the flex demand each position absorbs.
    """
    flex_slots = sum(
        1 for slot in settings.starting_slots if slot in FLEX_SLOTS
    ) * settings.teams
    allocation = dict(counts)

    for _ in range(flex_slots):
        best_position, best_value = None, float("-inf")
        for position in FLEXIBLE:
            pool = by_position.get(position, [])
            index = allocation.get(position, 0)
            if index >= len(pool):
                continue
            value = pool[index].projected_points or 0.0
            if value > best_value:
                best_position, best_value = position, value
        if best_position is None:
            break
        allocation[best_position] += 1

    return allocation


def replacement_levels(
    settings: LeagueSettings, players: list[Player]
) -> tuple[dict[str, float], dict[str, int]]:
    """Replacement points and the depth each position is replaced at.

    Returns (replacement points by position, replacement rank by position).
    """
    by_position: dict[str, list[Player]] = {}
    for player in players:
        if player.projected_points is None:
            continue
        by_position.setdefault(player.position, []).append(player)
    for pool in by_position.values():
        pool.sort(key=lambda p: p.projected_points or 0.0, reverse=True)

    depth = allocate_flex(settings, by_position, dedicated_starters(settings))

    levels: dict[str, float] = {}
    for position, pool in by_position.items():
        index = min(depth.get(position, 0), len(pool) - 1)
        levels[position] = pool[max(index, 0)].projected_points or 0.0
    return levels, depth


def value_board(settings: LeagueSettings, players: list[Player]) -> list[Valuation]:
    """Every player, valued above replacement, best first.

    Replacement level is computed twice on purpose. The first pass uses raw
    points to find who the replacement is; the second re-values everyone with
    missed games backfilled at that replacement's per-game rate. Doing it in one
    pass would need the answer before it could be computed.
    """
    levels, depth = replacement_levels(settings, players)

    by_position: dict[str, list[Player]] = {}
    for player in players:
        if player.projected_points is None:
            continue
        by_position.setdefault(player.position, []).append(player)

    replacement_ppg: dict[str, float] = {}
    for position, pool in by_position.items():
        pool.sort(key=lambda p: p.projected_points or 0.0, reverse=True)
        index = min(depth.get(position, 0), len(pool) - 1)
        anchor = pool[max(index, 0)]
        replacement_ppg[position] = (anchor.projected_points or 0.0) / SEASON_GAMES

    board = []
    for player in players:
        if player.projected_points is None:
            continue
        rate = replacement_ppg.get(player.position, 0.0)
        board.append(
            Valuation(
                player=player,
                points=player.projected_points,
                games=player.games,
                adjusted=adjusted_points(player, rate),
                replacement=levels.get(player.position, 0.0),
            )
        )
    board.sort(key=lambda v: v.vor, reverse=True)
    return board
