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


#: Ranks are converted to value by halving every RANK_HALF_LIFE places.
#: A linear curve (1000 - rank) was wrong: it made the top of the board almost
#: flat, so the gap between the #1 and #11 player was ~1% while a "prefer this
#: position" bonus was 15%. Soft preferences then silently outranked value by
#: a hundred places. With a half-life curve, a 15% bonus is worth about six
#: ranks -- enough to break ties, not enough to overturn the board.
RANK_HALF_LIFE = 30.0
RANK_VALUE_BASE = 1000.0


def value_from_rank(rank: float) -> float:
    """Draft value implied by a consensus rank."""
    return RANK_VALUE_BASE * 0.5 ** (max(0.0, rank - 1.0) / RANK_HALF_LIFE)


def value_of(player: Player) -> float:
    """A single comparable number. Projections win; rank is the fallback."""
    if player.projected_points is not None:
        return player.projected_points
    if player.adp is not None:
        return value_from_rank(player.adp)
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


def expected_round(rank: float, teams: int) -> int:
    """Which round a player ranked `rank` is expected to go in."""
    return int(max(0.0, rank - 1.0) // teams) + 1


def rank_queue(
    strategy: Strategy, settings: LeagueSettings, available: list[Player]
) -> list[tuple[Player, float]]:
    """A static preference list for the platform's own autodraft.

    Different from `rank_board` in two ways, both because a queue is not a
    single pick. The reach limit is skipped -- it asks "is this too early *for
    this pick*", which is meaningless in a list covering every pick. And round
    gates are applied against the round a player is expected to go in, so a
    kicker gated until round 14 is dropped if he ranks inside round 3 but kept
    if he ranks where a round-15 pick would land.
    """
    scored: list[tuple[Player, float]] = []
    for player in available:
        if player.adp is None:
            continue

        # A position gated until round N doesn't remove the player, it delays
        # him. Dropping him outright would mean never taking the TE1 even if he
        # fell past the gate; demoting him to the gate's first pick keeps him
        # available exactly when the strategy allows.
        gate = strategy.earliest_round.get(player.position, 1)
        earliest_pick = (gate - 1) * settings.teams + 1
        effective_rank = max(player.adp, float(earliest_pick))

        round_number = expected_round(effective_rank, settings.teams)
        if not strategy.may_draft(player.position, round_number, 0):
            continue

        state = DraftState(round=round_number, pick=1)
        adjusted = player.model_copy(update={"adp": effective_rank})
        scored.append((player, score(adjusted, strategy, settings, state)))

    ranked = sorted(scored, key=lambda pair: pair[1], reverse=True)
    return ensure_startable_positions(ranked, strategy, settings)


def ensure_startable_positions(
    ranked: list[tuple[Player, float]],
    strategy: Strategy,
    settings: LeagueSettings,
    rounds: int | None = None,
) -> list[tuple[Player, float]]:
    """Guarantee the queue can actually fill the required starting lineup.

    Consensus rankings bury kickers and defenses below the last pick of the
    draft -- they are near-worthless on a points-per-rank basis. But a roster
    with a K slot and a DST slot must fill them, and an autodraft working off a
    pure value ranking would end the draft with holes. So for each position
    with a dedicated starting slot, promote the best candidates to just after
    the round the strategy allows them.
    """
    window = (rounds or len(settings.roster_slots)) * settings.teams
    result = list(ranked)

    for position in sorted(set(settings.starting_slots)):
        required = settings.starters_at(position)
        if not required:
            continue
        present = sum(1 for p, _ in result[:window] if p.position == position)
        if present >= required:
            continue

        # A gate can be later than the draft is long (K gated to round 14 in a
        # ten-round league). Honour it where possible, but never past the last
        # pick -- a player placed beyond the end of the draft is the same as
        # not being in the queue at all.
        gate = strategy.earliest_round.get(position, 1)
        insert_at = min((gate - 1) * settings.teams, window - required, len(result))
        insert_at = max(insert_at, 0)
        candidates = [pair for pair in result[window:] if pair[0].position == position]
        promoting = candidates[: required - present]
        if not promoting:
            continue
        for pair in promoting:
            result.remove(pair)
        result[insert_at:insert_at] = promoting

    return result
