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
import multiprocessing
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

#: The round from which simulated opponents will consider a kicker or defence.
#: Rooms vary enormously here and it changes the advice: a room that waits until
#: round 14 leaves the best kicker on the board far longer than one that starts
#: in round 8, so guessing wrong makes the model reach for a position that was
#: never going anywhere. Set it from what the live board is actually doing.
KDST_FROM_ROUND = 8


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

    # Kickers and defences only late, as real rooms do. This has to be applied
    # to the whole pool rather than to the shortlist: by the middle rounds the
    # top of the ADP board *is* kickers and defences, so filtering a four-man
    # slice leaves nothing and the fallback takes one regardless. That made the
    # round gate inert -- every setting predicted the first kicker at the same
    # pick -- and the model could not represent a room that waits.
    early = len(roster) + 1 < KDST_FROM_ROUND
    if early:
        candidates = [v for v in pool if v.player.position not in ("K", "DST")]
        pool = candidates or pool

    by_adp = sorted((v for v in pool if v.player.adp), key=lambda v: v.player.adp)
    if not by_adp:
        by_adp = sorted(pool, key=lambda v: -v.points)
    shortlist = by_adp[:noise]
    if wanted and rng.random() < need_bias:
        fits = [v for v in by_adp[: noise * 4] if v.player.position in wanted]
        if fits:
            shortlist = fits[:noise]
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


#: Built once per worker process. The board is several hundred objects and the
#: sequences number in the hundreds, so shipping it with every task would cost
#: more than the simulation it feeds.
_CTX: dict = {}


def _init_worker(league: str) -> None:
    settings = load_settings(league)
    _CTX["settings"] = settings
    _CTX["board"] = value_board(settings, load_players(league))


def _score_sequence(job):
    seq, slot, teams, rounds, runs = job
    settings, board = _CTX["settings"], _CTX["board"]
    strategy = opening(list(seq))
    scores = [run(strategy, board, settings, slot, teams, rounds, seed)[0]
              for seed in range(runs)]
    return seq, statistics.mean(scores), statistics.stdev(scores) / (runs ** 0.5)


DEFAULT_CORE = ("RB", "WR", "TE", "QB")


def valid_openings(settings, depth, prefix=(), core=DEFAULT_CORE):
    """Every opening of `depth` picks you could actually start.

    `prefix` pins the picks already made, so the sweep answers the question you
    still have rather than re-deciding rounds that are over.
    """
    free = depth - len(prefix)
    return [tuple(prefix) + x for x in itertools.product(core, repeat=free)
            if all(Counter(tuple(prefix) + x)[p] <= roster_cap(p, settings)
                   for p in tuple(prefix) + x)]


def summarise(scored, depth, fixed=0):
    """Where the signal is, once hundreds of sequences are too many to read.

    Two cuts. First, what a position is worth *in a given round* -- averaged
    over every sequence that puts it there, so the other picks wash out. Second,
    how many of each position the opening should contain at all. Both are far
    more stable than any single sequence, because each average is built from
    hundreds of drafts rather than a hundred.
    """
    cols = sorted({p for _, _, seq in scored for p in seq})
    print()
    print("  value of each position by round, averaged over every opening")
    print()
    print(f"  {'round':>6}" + "".join(f"{p:>9}" for p in cols))
    for rnd in range(fixed, depth):
        cells = ""
        for pos in cols:
            at = [m for m, _, seq in scored if seq[rnd] == pos]
            cells += f"{statistics.mean(at):>9.0f}" if at else f"{'-':>9}"
        print(f"  {rnd + 1:>6}{cells}")

    print()
    print(f"  how many of each position belong in the first {depth} picks")
    print()
    print(f"  {'count':>6}" + "".join(f"{p:>9}" for p in cols))
    for n in range(depth + 1):
        cells = ""
        for pos in cols:
            at = [m for m, _, seq in scored if Counter(seq)[pos] == n]
            cells += f"{statistics.mean(at):>9.0f}" if at else f"{'-':>9}"
        print(f"  {n:>6}{cells}")


def openings(board, settings, slot, teams, rounds, runs, depth,
             jobs=1, prefix=(), core=DEFAULT_CORE, show=25):
    """Which opening sequence of positions ends up with the best lineup?

    Every sequence runs against the same numbered seeds, so the comparison is
    paired: the rooms differ between sequences only in response to what was
    taken, not in their own randomness. The spread between good sequences is
    small next to run-to-run noise, so the standard error is reported -- without
    it these numbers invite conclusions they cannot support.
    """
    seqs = valid_openings(settings, depth, prefix, core)
    jobs = max(1, min(jobs, len(seqs)))
    pinned = f", first {len(prefix)} pinned to {' '.join(prefix)}" if prefix else ""
    print(f"  opening {depth} picks, {runs} runs each, {len(seqs)} sequences, "
          f"{len(seqs) * runs} drafts on {jobs} core(s){pinned}")

    tasks = [(seq, slot, teams, rounds, runs) for seq in seqs]
    if jobs > 1:
        with multiprocessing.Pool(jobs, _init_worker, (settings.key,)) as pool:
            results = pool.map(_score_sequence, tasks, chunksize=4)
    else:
        _CTX["settings"], _CTX["board"] = settings, board
        results = [_score_sequence(t) for t in tasks]

    scored = sorted(((m, e, seq) for seq, m, e in results), reverse=True)

    print()
    print(f"  top {min(show, len(scored))} openings")
    print()
    print(f"  {'opening':<26}{'mean':>9}{'+/-':>7}")
    for mean, err, seq in scored[:show]:
        print(f"  {' '.join(seq):<26}{mean:>9.0f}{err:>7.0f}")
    if len(scored) > show:
        print()
        print("  worst 5")
        for mean, err, seq in scored[-5:]:
            print(f"  {' '.join(seq):<26}{mean:>9.0f}{err:>7.0f}")

    top = scored[0]
    close = [x for x in scored if x[0] >= top[0] - (top[1] + x[1])]
    verdict = "a real edge" if len(close) == 1 else "not separable at this sample"
    print()
    print(f"  best: {' '.join(top[2])} -- {len(close)} sequence(s) within one "
          f"standard error, {verdict}.")

    summarise(scored, depth, fixed=len(prefix))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="sleeper")
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--teams", type=int, default=None)
    parser.add_argument("--runs", type=int, default=120)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--core", default=" ".join(DEFAULT_CORE),
                        help="positions the sweep may choose from")
    parser.add_argument("--prefix", default="",
                        help="picks already made, e.g. 'RB RB' -- pinned, not re-decided")
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
                        args.runs, args.openings, args.jobs,
                        tuple(args.prefix.split()), tuple(args.core.split()))

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
