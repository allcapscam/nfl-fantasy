"""The decision engine: given the board, a strategy, and a league, pick a player."""

from __future__ import annotations

from collections import Counter

from nfl_fantasy.platforms.base import DraftState, Player
from nfl_fantasy.roster import need_multiplier
from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.strategy import Strategy

PREFER_BONUS = 1.15
AVOID_PENALTY = 0.5


def overall_pick_number(round_number: int, pick_in_round: int, teams: int) -> int:
    return (round_number - 1) * teams + pick_in_round


def value_of(player: Player) -> float:
    """A single comparable number. Projections win; ADP is the fallback."""
    if player.projected_points is not None:
        return player.projected_points
    if player.adp is not None:
        return max(0.0, 1000.0 - player.adp)
    return 0.0


def format_multiplier(position: str, strategy: Strategy, settings: LeagueSettings) -> float:
    """Adjustments that depend on the league's format, not your preferences."""
    multiplier = 1.0
    if position == "QB" and settings.is_superflex:
        multiplier *= strategy.superflex_qb_weight
    if position == "TE" and settings.scoring.is_te_premium:
        multiplier *= strategy.te_premium_weight
    return multiplier


def score(
    player: Player,
    strategy: Strategy,
    settings: LeagueSettings,
    state: DraftState,
) -> float:
    """Raw value adjusted by league format, roster need, and your preferences."""
    result = value_of(player)
    result *= strategy.position_weight.get(player.position, 1.0)
    result *= format_multiplier(player.position, strategy, settings)
    result *= need_multiplier(player.position, state.my_roster, settings)

    plan = strategy.plan_for_round(state.round)
    if plan:
        if player.position in plan.prefer:
            result *= PREFER_BONUS
        elif player.position in plan.avoid:
            result *= AVOID_PENALTY
    return result


def eligible(
    player: Player,
    state: DraftState,
    strategy: Strategy,
    settings: LeagueSettings,
    overall: int,
) -> bool:
    """Hard constraints: roster caps, position gates, and the reach limit."""
    rostered = Counter(p.position for p in state.my_roster)
    if not strategy.may_draft(player.position, state.round, rostered[player.position]):
        return False
    if player.id in state.drafted_player_ids:
        return False
    return not (
        player.adp is not None and player.adp - overall > strategy.reach_tolerance
    )


def rank_board(
    state: DraftState,
    strategy: Strategy,
    settings: LeagueSettings,
    available: list[Player],
) -> list[tuple[Player, float]]:
    """Every eligible player, best first. This is what the UI shows you."""
    overall = overall_pick_number(state.round, state.pick, settings.teams)
    scored = [
        (p, score(p, strategy, settings, state))
        for p in available
        if eligible(p, state, strategy, settings, overall)
    ]
    return sorted(scored, key=lambda pair: pair[1], reverse=True)


def choose_pick(
    state: DraftState,
    strategy: Strategy,
    settings: LeagueSettings,
    available: list[Player],
) -> Player | None:
    """The single best eligible player, or None if nothing qualifies."""
    ranked = rank_board(state, strategy, settings, available)
    return ranked[0][0] if ranked else None
