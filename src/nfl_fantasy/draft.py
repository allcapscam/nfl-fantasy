"""The decision engine: given the board and a strategy, pick a player."""

from __future__ import annotations

from collections import Counter

from nfl_fantasy.platforms.base import DraftState, Player
from nfl_fantasy.strategy import Strategy

PREFER_BONUS = 1.15
AVOID_PENALTY = 0.5


def overall_pick_number(round_number: int, pick_in_round: int, teams: int) -> int:
    """Convert a (round, pick) pair into an overall pick number."""
    return (round_number - 1) * teams + pick_in_round


def value_of(player: Player) -> float:
    """A single comparable number for a player.

    Projections are preferred; ADP is the fallback so the bot still works
    before projections are wired up.
    """
    if player.projected_points is not None:
        return player.projected_points
    if player.adp is not None:
        return max(0.0, 1000.0 - player.adp)
    return 0.0


def score(player: Player, strategy: Strategy, round_number: int) -> float:
    """Apply the strategy's soft preferences to a player's raw value."""
    result = value_of(player) * strategy.position_weight.get(player.position, 1.0)
    plan = strategy.plan_for_round(round_number)
    if plan:
        if player.position in plan.prefer:
            result *= PREFER_BONUS
        elif player.position in plan.avoid:
            result *= AVOID_PENALTY
    return result


def eligible(
    player: Player, state: DraftState, strategy: Strategy, overall: int
) -> bool:
    """Hard constraints: roster caps, position gates, and reach limit."""
    rostered = Counter(p.position for p in state.my_roster)
    if not strategy.may_draft(player.position, state.round, rostered[player.position]):
        return False
    if player.id in state.drafted_player_ids:
        return False
    return not (
        player.adp is not None and player.adp - overall > strategy.reach_tolerance
    )


def choose_pick(
    state: DraftState, strategy: Strategy, available: list[Player]
) -> Player | None:
    """The highest-scoring player who satisfies every hard constraint.

    Returns None when nothing is eligible -- the caller should fall back to
    best-available or hand control back to a human.
    """
    overall = overall_pick_number(state.round, state.pick, strategy.league.teams)
    candidates = [p for p in available if eligible(p, state, strategy, overall)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: score(p, strategy, state.round))
