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

from collections import Counter
from dataclasses import dataclass

from nfl_fantasy.settings import FLEX_SLOTS, LeagueSettings, slot_accepts
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


def team_at_pick(overall: int, teams: int) -> int:
    """Which draft slot owns a given overall pick, accounting for the snake."""
    index = (overall - 1) % teams
    round_number = (overall - 1) // teams + 1
    return index + 1 if round_number % 2 == 1 else teams - index


def rosters_from_picks(positions: list[str], teams: int) -> dict[int, Counter]:
    """Reconstruct every team's roster from the picks made so far.

    `positions` is the position of each pick in draft order, so its index is
    the overall pick number.
    """
    rosters: dict[int, Counter] = {slot: Counter() for slot in range(1, teams + 1)}
    for index, position in enumerate(positions, start=1):
        rosters[team_at_pick(index, teams)][position] += 1
    return rosters


def team_needs(settings: LeagueSettings, roster: Counter) -> dict[str, float]:
    """Starting slots this team still has to fill, by position.

    Flex demand is spread over the positions that can fill it, so a team with
    its dedicated RB and WR slots full still carries some appetite for both.
    """
    needs: dict[str, float] = {}
    for position in POSITIONS:
        required = settings.starters_at(position)
        needs[position] = max(0.0, required - roster.get(position, 0))

    flex_slots = sum(1 for slot in settings.starting_slots if slot in FLEX_SLOTS)
    if flex_slots:
        dedicated_filled = all(
            roster.get(p, 0) >= settings.starters_at(p) for p in ("RB", "WR", "TE")
        )
        flex_used = max(
            0,
            sum(roster.get(p, 0) - settings.starters_at(p) for p in ("RB", "WR", "TE")),
        )
        remaining_flex = max(0.0, flex_slots - flex_used)
        if remaining_flex and dedicated_filled:
            for position in ("RB", "WR"):
                needs[position] += remaining_flex / 2
    return needs


def runs_from_needs(
    settings: LeagueSettings,
    positions_taken: list[str],
    start: int,
    end: int,
    my_slot: int,
    teams: int | None = None,
) -> dict[str, float]:
    """Expected positional runs from what the teams picking next actually need.

    This is the correction to a momentum model. If every team has already
    filled its running back slots, another run on backs is *less* likely, not
    more -- demand is spent. Each opposing pick in the window is spread across
    that team's unfilled slots.
    """
    runs = dict.fromkeys(POSITIONS, 0.0)
    teams = teams or settings.teams
    rosters = rosters_from_picks(positions_taken, teams)

    for overall in range(start + 1, end + 1):
        slot = team_at_pick(overall, teams)
        if slot == my_slot:
            continue
        needs = team_needs(settings, rosters[slot])
        total = sum(needs.values())
        if total <= 0:
            # Starting lineup complete: this team drafts depth, which follows
            # value rather than need. The ADP prior covers that case.
            continue
        for position, need in needs.items():
            runs[position] += need / total
    return runs


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


#: Round by which drafting for need dominates drafting best-available. Nobody
#: drafts for need in round one; by the middle rounds almost everyone does.
NEED_DRIVEN_BY_ROUND = 9.0
MAX_NEED_WEIGHT = 0.8


def need_weight(round_number: int) -> float:
    """How much to trust roster need over ADP at this stage of the draft."""
    progress = max(0.0, round_number - 1) / (NEED_DRIVEN_BY_ROUND - 1)
    return min(MAX_NEED_WEIGHT, progress * MAX_NEED_WEIGHT)


def blend_runs(
    prior: dict[str, float], needs: dict[str, float], round_number: int
) -> dict[str, float]:
    """Combine the ADP prior with what the teams picking next still need.

    Early rounds are best-player-available, so ADP carries it. Later, rosters
    are half full and picks are driven by holes, so the need model takes over.
    """
    weight = need_weight(round_number)
    return {
        position: prior.get(position, 0.0) * (1 - weight)
        + needs.get(position, 0.0) * weight
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


#: Penalty for piling another bye onto a week that is already thin. Byes are
#: invisible to value models, but a week where four starters sit is a game you
#: probably lose, and one more body there makes it worse. Recommending a
#: bye-week-11 kicker into exactly that week is what prompted this.
BYE_CROWDING_PENALTY = 0.55
BYE_CROWDED_AT = 3


def bye_conflict(
    bye: int | None, roster_byes: dict[int, int] | None
) -> tuple[float, str | None]:
    """Discount a player whose bye lands on an already-crowded week."""
    if not bye or not roster_byes:
        return 1.0, None
    stacked = roster_byes.get(bye, 0)
    if stacked >= BYE_CROWDED_AT:
        return BYE_CROWDING_PENALTY, f"week {bye} already has {stacked} on bye"
    return 1.0, None


#: What a player who does not crack the starting lineup is actually worth.
#: Value above replacement measures production, but production only counts if
#: it enters your lineup. A fourth running back behind three starters scores
#: nothing most weeks; he pays out on an injury or a bye, which is real but a
#: fraction of what his raw value suggests.
BENCH_VALUE = 0.35


def starts_immediately(
    position: str,
    roster_counts: dict[str, int],
    settings: LeagueSettings,
    open_slots: list[str] | None = None,
) -> bool:
    """Would this player walk into the starting lineup?

    When the caller can supply the actually-unfilled slots, use them. Counting
    positions instead double-books the flex: `max_startable` credits the flex to
    every eligible position independently, so with a third running back already
    in it, a third receiver still reads as a starter when he would be bench.
    """
    if open_slots is not None:
        return any(slot_accepts(slot, position) for slot in open_slots)
    return roster_counts.get(position, 0) < settings.max_startable(position)


def lineup_multiplier(
    position: str,
    roster_counts: dict[str, int],
    settings: LeagueSettings,
    open_slots: list[str] | None = None,
) -> float:
    """Discount a player who would sit on your bench.

    This corrects two mistakes seen in a live draft: the model offered a second
    quarterback in round five behind an established starter, and preferred a
    fourth running back to a tight end when the tight end slot was still empty.
    Both come from comparing value without asking whether the player ever
    enters the lineup.
    """
    starts = starts_immediately(position, roster_counts, settings, open_slots)
    return 1.0 if starts else BENCH_VALUE


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


@dataclass
class Candidate:
    """One player worth taking, and what passing on him costs."""

    valuation: Valuation
    depth: int
    expected_next: float
    runs: float
    upside: float = 1.0
    upside_note: str | None = None
    lineup: float = 1.0
    bye_penalty: float = 1.0
    bye_note: str | None = None
    market_note: str | None = None

    @property
    def position(self) -> str:
        return self.valuation.player.position

    @property
    def starts(self) -> bool:
        return self.lineup >= 1.0

    @property
    def value(self) -> float:
        """Value in the role this player would actually fill."""
        return self.valuation.vor if self.starts else self.valuation.bench_vor

    @property
    def cost_of_waiting(self) -> float:
        gap = self.value - self.expected_next
        return gap * self.upside * self.lineup * self.bye_penalty


def candidates(
    available: list[Valuation],
    runs: dict[str, float],
    settings: LeagueSettings,
    roster_counts: dict[str, int] | None = None,
    per_position: int = 3,
    open_slots: list[str] | None = None,
    roster_byes: dict[int, int] | None = None,
) -> list[Candidate]:
    """Individual players ranked by what passing on them would cost.

    `opportunity_costs` answers "which position", comparing only each position's
    best player. This answers "which player", which needs a per-player version
    of the same subtraction: the second-best back is measured against the back
    who would be there after the run, one place deeper down the same list.
    """
    roster_counts = roster_counts or {}
    by_position: dict[str, list[Valuation]] = {}
    for valuation in available:
        by_position.setdefault(valuation.player.position, []).append(valuation)

    results: list[Candidate] = []
    for position, pool in by_position.items():
        if roster_counts.get(position, 0) >= roster_cap(position, settings):
            continue
        pool.sort(key=lambda v: v.vor, reverse=True)
        vors = [v.vor for v in pool]
        run = runs.get(position, 0.0)
        for depth, valuation in enumerate(pool[:per_position]):
            results.append(
                Candidate(
                    valuation=valuation,
                    depth=depth,
                    # Take this player and the next one you get at this position
                    # is `run` places further down from where he sat.
                    expected_next=interpolate(vors, depth + run),
                    runs=run,
                    lineup=lineup_multiplier(
                        position, roster_counts, settings, open_slots
                    ),
                    bye_penalty=bye_conflict(
                        valuation.player.bye_week, roster_byes
                    )[0],
                    bye_note=bye_conflict(
                        valuation.player.bye_week, roster_byes
                    )[1],
                )
            )
    results.sort(key=lambda c: c.cost_of_waiting, reverse=True)
    return results


def diversify(
    ranked: list[Candidate], count: int = 4, min_positions: int = 2
) -> list[Candidate]:
    """Take the best `count`, forcing at least `min_positions` represented.

    A shortlist that is four running backs is not a shortlist, it is one
    recommendation with spares. Showing a genuine alternative at another
    position is what makes the advice usable when you disagree with the model.
    """
    if not ranked:
        return []

    # Show each position's best player, not whichever happens to have the
    # steepest drop. Ranking by drop is right for choosing a position; applied
    # to a list it once hid the best defence on the board behind two worse ones.
    best_at: dict[str, Candidate] = {}
    for candidate in ranked:
        current = best_at.get(candidate.position)
        if current is None or candidate.value > current.value:
            best_at[candidate.position] = candidate
    ranked = sorted(
        {id(c): c for c in list(best_at.values()) + ranked}.values(),
        key=lambda c: c.cost_of_waiting,
        reverse=True,
    )

    chosen: list[Candidate] = []
    seen_best: set[str] = set()
    for candidate in ranked:
        if len(chosen) >= count:
            break
        # Never offer a deeper player while that position's best is still unshown.
        if candidate is not best_at.get(candidate.position)                 and candidate.position not in seen_best:
            continue
        seen_best.add(candidate.position)
        # Keep at most two from any one position while the shortlist is short,
        # so a single deep position cannot crowd out every alternative.
        same = sum(1 for c in chosen if c.position == candidate.position)
        if same >= max(1, count - min_positions):
            continue
        chosen.append(candidate)

    represented = {c.position for c in chosen}
    if len(represented) < min_positions:
        for candidate in ranked:
            if candidate.position not in represented:
                chosen.append(candidate)
                represented.add(candidate.position)
                if len(represented) >= min_positions:
                    break
    return chosen[:count]


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
