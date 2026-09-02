"""Monte Carlo the draft to compare strategies from a given slot.

The model recommends one pick at a time. This asks a different question: across
a whole draft, against a room that drafts sensibly, which overall approach ends
up with the best starting lineup? Running it many times separates a strategy
that is genuinely better from one that got a good board.

Opponents are modelled as reasonable, not optimal: they mostly take near the top
of ADP, they prefer a position they still need to start, and they pick with some
randomness rather than deterministically. A room of perfect drafters would be
both unrealistic and a harder test than the one being played.
"""

from __future__ import annotations

import argparse
import itertools
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nfl_fantasy.advisor import load_players
from nfl_fantasy.settings import LeagueSettings, slot_accepts
from nfl_fantasy.store import load_settings
from nfl_fantasy.valuation import Valuation, value_board
from nfl_fantasy.vona import (
    blend_runs,
    candidates,
    next_pick_after,
    roster_cap,
    runs_from_adp,
    runs_from_needs,
    snake_picks,
    team_at_pick,
)

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


def lineup_points(roster: list[Valuation], settings: LeagueSettings) -> float:
    """Best legal starting lineup from a roster, in projected points."""
    slots = settings.starting_slots
    remaining = sorted(roster, key=lambda v: -v.points)
    total = 0.0
    dedicated = [s for s in slots if s not in ("FLEX",)]
    flex = [s for s in slots if s == "FLEX"]
    for slot in dedicated + flex:
        pick = next((v for v in remaining if slot_accepts(slot, v.player.position)), None)
        if pick:
            remaining.remove(pick)
            total += pick.points
    return total


def needs(roster: list[Valuation], settings: LeagueSettings) -> set[str]:
    have = Counter(v.player.position for v in roster)
    return {p for p in POSITIONS
            if have[p] < settings.starters_at(p) + (1 if p in ("RB", "WR") else 0)}


def opponent_pick(pool, roster, settings, rng, need_bias=0.65, noise=4):
    """A sensible manager: near the top of ADP, leaning to a position they need."""
    wanted = needs(roster, settings)
    by_adp = sorted((v for v in pool if v.player.adp), key=lambda v: v.player.adp)
    if not by_adp:
        by_adp = sorted(pool, key=lambda v: -v.points)
    shortlist = by_adp[:noise]
    if wanted and rng.random() < need_bias:
        fits = [v for v in by_adp[: noise * 4] if v.player.position in wanted]
        if fits:
            shortlist = fits[:noise]
    # Kickers and defences only late, as real rooms do.
    late = len(roster) >= len(settings.starting_slots) - 2
    if not late:
        filtered = [v for v in shortlist if v.player.position not in ("K", "DST")]
        shortlist = filtered or shortlist
    return rng.choice(shortlist)


# -- strategies under test ---------------------------------------------------


def strat_vona(pool, roster, settings, slot, teams, rounds, pick_no, taken_positions):
    following = next_pick_after(pick_no, slot, teams, rounds)
    rnd = (pick_no - 1) // teams + 1
    adp = {v.player.name: v.player.adp for v in pool if v.player.adp}
    pos = {v.player.name: v.player.position for v in pool}
    prior = runs_from_adp(adp, pos, pick_no, following or pick_no)
    need = runs_from_needs(settings, taken_positions, pick_no,
                           following or pick_no, slot, teams=teams)
    runs = blend_runs(prior, need, rnd)
    counts = Counter(v.player.position for v in roster)
    ranked = candidates(pool, runs, settings, dict(counts))
    if not ranked:
        ranked = candidates(pool, runs, settings, {})
    return ranked[0].valuation if ranked else max(pool, key=lambda v: v.points)


def strat_best_vor(pool, roster, settings, *_):
    counts = Counter(v.player.position for v in roster)
    ok = [v for v in pool
          if counts[v.player.position] < roster_cap(v.player.position, settings)]
    return max(ok or pool, key=lambda v: v.vor)


def strat_adp(pool, roster, settings, *_):
    """Take the top of the board, but stop at a position you cannot start.

    Without the cap this is a strawman that drafts five quarterbacks. A manager
    who simply follows consensus still notices when a slot is already full.
    """
    counts = Counter(v.player.position for v in roster)
    ok = [v for v in pool if v.player.adp
          and counts[v.player.position] < roster_cap(v.player.position, settings)]
    return min(ok or pool, key=lambda v: v.player.adp or 9e9)


def _positional(order):
    def strategy(pool, roster, settings, *_):
        counts = Counter(v.player.position for v in roster)
        rnd = len(roster) + 1
        want = order[rnd - 1] if rnd <= len(order) else None
        if want:
            fits = [v for v in pool if v.player.position == want
                    and counts[want] < roster_cap(want, settings)]
            if fits:
                return max(fits, key=lambda v: v.vor)
        return strat_best_vor(pool, roster, settings)
    return strategy


STRATEGIES = {
    "VONA (the model)": strat_vona,
    "best value available": strat_best_vor,
    "follow ADP": strat_adp,
    "RB-heavy start": _positional(["RB", "RB", "RB", "WR", "WR", "TE"]),
    "WR-heavy start": _positional(["WR", "WR", "WR", "RB", "RB", "TE"]),
    "balanced RB/WR": _positional(["RB", "WR", "RB", "WR", "TE", "WR"]),
    "elite TE early": _positional(["RB", "WR", "TE", "RB", "WR", "WR"]),
}


def opening(sequence):
    """Force the first rounds to given positions, then hand over to the model."""
    def strategy(pool, roster, settings, slot, teams, rounds, pick_no, taken):
        rnd = len(roster) + 1
        if rnd <= len(sequence):
            want = sequence[rnd - 1]
            counts = Counter(v.player.position for v in roster)
            fits = [v for v in pool if v.player.position == want
                    and counts[want] < roster_cap(want, settings)]
            if fits:
                return max(fits, key=lambda v: v.vor)
        return strat_vona(pool, roster, settings, slot, teams, rounds, pick_no, taken)
    return strategy


def run(strategy, board, settings, slot, teams, rounds, seed):
    rng = random.Random(seed)
    pool = list(board)
    mine: list[Valuation] = []
    my_picks = set(snake_picks(slot, teams, rounds))
    taken_positions: list[str] = []
    rosters: dict[int, list[Valuation]] = {s: [] for s in range(1, teams + 1)}

    for overall in range(1, rounds * teams + 1):
        if not pool:
            break
        if overall in my_picks:
            choice = strategy(pool, mine, settings, slot, teams, rounds,
                              overall, taken_positions)
            mine.append(choice)
        else:
            team = team_at_pick(overall, teams)
            choice = opponent_pick(pool, rosters[team], settings, rng)
            rosters[team].append(choice)
        pool.remove(choice)
        taken_positions.append(choice.player.position)
    return lineup_points(mine, settings), mine


def openings(board, settings, slot, teams, rounds, runs, depth):
    """Which opening sequence of positions ends up with the best lineup?

    Every sequence runs against the same numbered seeds, so the comparison is
    paired: the rooms differ between sequences only in response to what was
    taken, not in their own randomness. The spread between good sequences is
    small next to run-to-run noise, so the standard error is reported -- without
    it these numbers invite conclusions they cannot support.
    """
    core = ["RB", "WR", "TE", "QB"]
    seqs = [tuple(x) for x in itertools.product(core, repeat=depth)]
    seqs = [x for x in seqs
            if all(Counter(x)[p] <= roster_cap(p, settings) for p in x)]

    scored = []
    for seq in seqs:
        strategy = opening(list(seq))
        scores = [run(strategy, board, settings, slot, teams, rounds, seed)[0]
                  for seed in range(runs)]
        scored.append((statistics.mean(scores),
                       statistics.stdev(scores) / (runs ** 0.5), seq))
    scored.sort(reverse=True)

    print(f"  opening {depth} picks, {runs} runs each, {len(seqs)} sequences")
    print(f"  {'opening':<22}{'mean':>9}{'+/-':>7}")
    for mean, err, seq in scored:
        print(f"  {' '.join(seq):<22}{mean:>9.0f}{err:>7.0f}")
    top = scored[0]
    close = [x for x in scored if x[0] >= top[0] - (top[1] + x[1])]
    verdict = "a real edge" if len(close) == 1 else "not separable at this sample"
    print(f"\n  best: {' '.join(top[2])} -- {len(close)} sequence(s) within one "
          f"standard error, {verdict}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="sleeper")
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--teams", type=int, default=None)
    parser.add_argument("--runs", type=int, default=120)
    parser.add_argument("--openings", type=int, default=0,
                        help="compare forced opening sequences of this many rounds")
    args = parser.parse_args()

    settings = load_settings(args.league)
    teams = args.teams or settings.teams
    rounds = len([s for s in settings.roster_slots if s != "IR"])
    board = value_board(settings, load_players(args.league))

    print(f"{args.league}: slot {args.slot} of {teams}, {rounds} rounds, "
          f"picks {snake_picks(args.slot, teams, rounds)[:6]}...\n")
    if args.openings:
        return openings(board, settings, args.slot, teams, rounds,
                        args.runs, args.openings)

    print(f"  {'strategy':<24}{'mean':>9}{'median':>9}{'worst':>9}{'best':>9}")
    results = {}
    for name, strategy in STRATEGIES.items():
        scores = [run(strategy, board, settings, args.slot, teams, rounds, seed)[0]
                  for seed in range(args.runs)]
        results[name] = scores
        print(f"  {name:<24}{statistics.mean(scores):>9.0f}"
              f"{statistics.median(scores):>9.0f}{min(scores):>9.0f}{max(scores):>9.0f}")

    best = max(results, key=lambda k: statistics.mean(results[k]))
    base = statistics.mean(results["follow ADP"])
    print(f"\n  best: {best} "
          f"({statistics.mean(results[best]) - base:+.0f} vs following ADP)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
