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

Which flex slots, and what each one accepts, is the whole of the difference
between a single-QB league and a superflex one. A W/R/T seat is competition
among backs, receivers and tight ends; a Q/W/R/T seat is a seat a quarterback
almost always wins. Treating every flex slot as the first kind put twelve
superflex seats into the RB/WR pool and left quarterbacks replaced at QB12 in a
league where twenty-four of them start -- so no quarterback appeared anywhere
near the top of the board. Nothing here hardcodes which positions are flexible;
it is read off the slots the league actually has.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import FLEX_SLOTS, LeagueSettings

#: Games in a fantasy regular season plus playoffs, less the bye. Yahoo's own
#: projected-games column tops out here, so the two agree.
SEASON_GAMES = 16

#: What a plain flex slot accepts. Only a fallback for callers with no league
#: to inspect -- anything that has the settings must ask them, because a
#: superflex seat accepts a quarterback and this tuple does not.
FLEXIBLE = ("RB", "WR", "TE")


def flex_seats(settings: LeagueSettings) -> list[str]:
    """Every flex slot in one team's starting lineup, narrowest seat first.

    Order matters when the seats differ: a W/R/T seat cannot hold the
    quarterback a Q/W/R/T seat can, so the choosier seat picks first and the
    open one settles for whoever is left.
    """
    return sorted(
        (slot for slot in settings.starting_slots if slot in FLEX_SLOTS),
        key=lambda slot: len(FLEX_SLOTS[slot]),
    )


def flex_slot_kinds(settings: LeagueSettings) -> list[str]:
    """The distinct kinds of flex seat this league has, narrowest first."""
    seen: list[str] = []
    for slot in flex_seats(settings):
        if slot not in seen:
            seen.append(slot)
    return seen


@dataclass(frozen=True)
class Valuation:
    """What a player is worth, and the pieces that make it up."""

    player: Player
    points: float
    games: int | None
    adjusted: float
    replacement: float
    #: Replacement points for each kind of flex seat, keyed by slot name. A
    #: league with both a W/R/T and a Q/W/R/T seat has two, and they are not
    #: interchangeable -- the second pools quarterbacks in and cuts far deeper.
    flex_replacement: dict[str, float] = field(default_factory=dict)

    @property
    def vor(self) -> float:
        """Value over replacement -- the number to compare across positions."""
        return self.adjusted - self.replacement

    def flex_vor(self, slot: str) -> float:
        """Value when the seat being filled is `slot`.

        A dedicated slot only one position can fill makes that position's own
        replacement the right baseline. A flex slot is different: every position
        the seat accepts competes for the *same* seat, so the honest comparison
        is against the best of them who would not start anywhere -- one number,
        shared by all of them.

        Using positional VOR here inverts real choices. Tight end replacement is
        low, so a tight end's VOR flatters him: in a live draft the model rated
        a 158-point tight end above a 176-point receiver for a flex slot, which
        would have started eighteen fewer points every week.

        The seat has to be named, with no default, because leagues have more
        than one kind. A quarterback measured against the W/R/T pool scores
        hundreds of points of nonsense, since that pool is cut where receivers
        run out rather than where quarterbacks do. An unknown seat raises
        rather than quietly returning his whole projection as value -- a loud
        failure beats a plausible wrong number on the clock.
        """
        if slot not in self.flex_replacement:
            raise KeyError(
                f"no replacement level for seat {slot!r}; this league has "
                f"{sorted(self.flex_replacement) or 'no flex seats'}"
            )
        return self.adjusted - self.flex_replacement[slot]

    @property
    def bench_vor(self) -> float:
        """Value if this player will sit rather than start.

        The games backfill credits a player with replacement production for the
        weeks he misses, which is right for a starter you would stream around.
        For a bench player it is phantom value: you already have someone in that
        slot, so there is nothing to backfill. Judging a backup on the adjusted
        figure overrates exactly the injury-prone players a backup exists to
        insure against.
        """
        return self.points - self.replacement

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
    """Hand each flex seat to whichever eligible position has the better player.

    A fixed split (say 60/40 RB/WR) would be a guess. Assigning greedily by the
    value actually on the board lets the league's own player pool decide how
    much of the flex demand each position absorbs.

    Each seat draws only from the positions *it* accepts. Pooling every flex
    seat and drawing from one hardcoded list is what broke superflex: the
    Q/W/R/T seats were handed to backs and receivers, so quarterbacks kept a
    replacement level set by their dedicated slot alone.
    """
    allocation = dict(counts)

    # One pass per team, so the seats fill the way a league fills them rather
    # than one team taking every W/R/T before anyone takes a Q/W/R/T.
    for _ in range(settings.teams):
        for slot in flex_seats(settings):
            eligible = FLEX_SLOTS[slot]
            best_position, best_value = None, float("-inf")
            for position in eligible:
                pool = by_position.get(position, [])
                index = allocation.get(position, 0)
                if index >= len(pool):
                    continue
                value = pool[index].projected_points or 0.0
                if value > best_value:
                    best_position, best_value = position, value
            if best_position is not None:
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


def flex_replacement_levels(
    settings: LeagueSettings,
    players: list[Player],
    depth: dict[str, int] | None = None,
) -> dict[str, float]:
    """Points of the best player who would not start anywhere, per flex seat.

    Every position a seat accepts is pooled and ranked together, because for
    that seat that is exactly the competition. Whoever is left once the league's
    starting lineups are full is what you settle for if you spend the seat
    elsewhere -- so the cut uses the same allocation `replacement_levels` does,
    which already knows how many of each position start.

    Two seats give two numbers. In a league with both, the Q/W/R/T pool includes
    quarterbacks and cuts a full round deeper than the W/R/T pool, so measuring
    a quarterback against the latter would credit him with the gap between two
    unrelated scales.
    """
    by_position: dict[str, list[float]] = {}
    for player in players:
        if player.projected_points is None:
            continue
        by_position.setdefault(player.position, []).append(player.projected_points)
    for pool in by_position.values():
        pool.sort(reverse=True)

    if depth is None:
        indexed: dict[str, list[Player]] = {}
        for player in players:
            if player.projected_points is None:
                continue
            indexed.setdefault(player.position, []).append(player)
        for pool_players in indexed.values():
            pool_players.sort(key=lambda p: p.projected_points or 0.0, reverse=True)
        depth = allocate_flex(settings, indexed, dedicated_starters(settings))

    levels: dict[str, float] = {}
    for slot in flex_slot_kinds(settings):
        leftovers: list[float] = []
        for position in FLEX_SLOTS[slot]:
            pool = by_position.get(position, [])
            leftovers.extend(pool[depth.get(position, 0):])
        levels[slot] = max(leftovers) if leftovers else 0.0
    return levels


def value_board(settings: LeagueSettings, players: list[Player]) -> list[Valuation]:
    """Every player, valued above replacement, best first.

    Replacement level is computed twice on purpose. The first pass uses raw
    points to find who the replacement is; the second re-values everyone with
    missed games backfilled at that replacement's per-game rate. Doing it in one
    pass would need the answer before it could be computed.
    """
    levels, depth = replacement_levels(settings, players)
    flex_levels = flex_replacement_levels(settings, players, depth)

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
                flex_replacement=flex_levels,
            )
        )
    board.sort(key=lambda v: v.vor, reverse=True)
    return board
