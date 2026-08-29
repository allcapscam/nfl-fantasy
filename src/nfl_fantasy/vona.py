"""VONA: pick by what you lose, not by who is best.

The best player available is rarely the right pick. What matters is which
position degrades most before your next turn. If six running backs and half a
receiver will go while you wait, the running back you pass on costs you the gap
between RB1 and RB7, while the receiver costs you almost nothing -- so take the
back even when the receiver grades higher.

Formally, over two picks:

    take RB now  ->  RB1 + E[best WR at next pick]
    take WR now  ->  WR1 + E[best RB at next pick]

Subtracting, RB wins exactly when (RB1 - RB_next) > (WR1 - WR_next). Choosing
the largest drop *is* maximising the two-pick total; it is not a heuristic.

The expected number taken per position comes from ADP before the draft, and is
blended toward what the room is actually doing once picks are in -- rooms run
on positions far harder than ADP implies.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.valuation import Valuation

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


def snake_picks(slot: int, teams: int, rounds: int) -> list[int]:
    """Overall pick numbers for a given draft slot in a snake draft."""
    picks = []
    for rnd in range(1, rounds + 1):
        if rnd % 2 == 1:
            picks.append((rnd - 1) * teams + slot)
        else:
            picks.append((rnd - 1) * teams + (teams - slot + 1))
    return picks


def next_pick_after(current: int, slot: int, teams: int, rounds: int) -> int | None:
    """The overall number of your next turn after `current`."""
    return next((p for p in snake_picks(slot, teams, rounds) if p > current), None)


def interpolate(values: list[float], k: float) -> float:
    """Value of the k-th best remaining player, k fractional.

    With k = 3.5, half the time three are gone and you get the fourth; half the
    time four are gone and you get the fifth. So the expectation is the average
    of those two, which is what interpolating between them gives.
    """
    if not values:
        return 0.0
    if k <= 0:
        return values[0]
    low = int(k)
    if low >= len(values) - 1:
        return values[-1]
    fraction = k - low
    return values[low] * (1 - fraction) + values[low + 1] * fraction


def runs_from_adp(
    adp: dict[str, float], positions: dict[str, str], start: int, end: int
) -> dict[str, float]:
    """How many of each position ADP expects to go in (start, end]."""
    runs = dict.fromkeys(POSITIONS, 0.0)
    for name, value in adp.items():
        if start < value <= end:
            position = positions.get(name)
            if position in runs:
                runs[position] += 1.0
    return runs


def runs_observed(recent: list[str], gap: int) -> dict[str, float]:
    """Positional rate in the picks already made, scaled to a gap this size."""
    runs = dict.fromkeys(POSITIONS, 0.0)
    if not recent:
        return runs
    for position in recent:
        if position in runs:
            runs[position] += 1.0
    scale = gap / len(recent)
    return {position: count * scale for position, count in runs.items()}


def blend_runs(
    prior: dict[str, float], observed: dict[str, float], picks_seen: int, weight_at: int = 30
) -> dict[str, float]:
    """Move from the ADP prior toward the live room as evidence accumulates.

    Early on, a handful of picks is noise and ADP is the better guide. By about
    `weight_at` picks the room has shown its hand and deserves equal weight.
    """
    if picks_seen <= 0:
        return dict(prior)
    live = min(1.0, picks_seen / weight_at) * 0.5
    return {
        position: prior.get(position, 0.0) * (1 - live) + observed.get(position, 0.0) * live
        for position in POSITIONS
    }


#: Bench depth worth carrying beyond what you can start. You handcuff and cover
#: byes at the positions you start several of; a second kicker is never useful,
#: and a third tight end in a one-TE league is a wasted roster spot -- the flex
#: slot inflates TE demand far more than a flex is actually spent on one.
BENCH_ALLOWANCE = {"QB": 1, "RB": 1, "WR": 1, "TE": 0, "K": 0, "DST": 0}


def roster_cap(position: str, settings: LeagueSettings) -> int:
    """Most of a position worth rostering before it stops being an opportunity."""
    return settings.max_startable(position) + BENCH_ALLOWANCE.get(position, 0)


@dataclass
class Opportunity:
    """What taking this position now saves you."""

    position: str
    best: Valuation
    expected_next: float
    runs: float

    @property
    def cost_of_waiting(self) -> float:
        return self.best.vor - self.expected_next


def opportunity_costs(
    available: list[Valuation],
    runs: dict[str, float],
    settings: LeagueSettings,
    roster_counts: dict[str, int] | None = None,
) -> list[Opportunity]:
    """Cost of passing on each position, largest first."""
    roster_counts = roster_counts or {}
    by_position: dict[str, list[Valuation]] = {}
    for valuation in available:
        by_position.setdefault(valuation.player.position, []).append(valuation)

    results = []
    for position, pool in by_position.items():
        pool.sort(key=lambda v: v.vor, reverse=True)
        # A position you can no longer start is not an opportunity, it is a
        # bench flyer; it should not compete with a starting-lineup hole.
        if roster_counts.get(position, 0) >= roster_cap(position, settings):
            continue
        vors = [v.vor for v in pool]
        results.append(
            Opportunity(
                position=position,
                best=pool[0],
                expected_next=interpolate(vors, runs.get(position, 0.0)),
                runs=runs.get(position, 0.0),
            )
        )
    results.sort(key=lambda o: o.cost_of_waiting, reverse=True)
    return results


def missing_required(
    settings: LeagueSettings, roster_counts: dict[str, int], picks_left: int
) -> list[str]:
    """Required starting positions you cannot still fill in the picks remaining.

    K and DST are deliberately left to the maths rather than gated. This is the
    safety net for that choice: it warns rather than overrides.
    """
    missing = []
    for position in POSITIONS:
        required = settings.starters_at(position)
        if required and roster_counts.get(position, 0) < required:
            missing.append(position)
    return missing if len(missing) >= picks_left else []
