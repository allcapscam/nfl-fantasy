"""Simulate only what is left of the draft, on the clock.

The full sweep in `simulate.py` replays 192 picks from an empty board. Before a
draft that is the right question -- which opening shape wins. Mid-draft it is
almost all wasted work: the picks behind you are settled facts, and the question
is which of these five names to take right now.

So this starts from the live board and plays out only the remainder. Two modes:

    --pick   (default) rank individual players for your next pick
    --next N            sweep position sequences for your next N picks

Board state comes from Sleeper's picks API when a draft id is given, and
otherwise from a hand-maintained taken file -- which is what Yahoo and ESPN
need, since neither exposes a live board.

    uv run python scripts/quick.py --league sleeper --slot 9 --draft-id 1389...
    uv run python scripts/quick.py --league yahoo2 --slot 5 --next 3
"""

from __future__ import annotations

import argparse
import itertools
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import simulate
from simulate import lineup_points, opponent_pick, strat_vona

from nfl_fantasy.advisor import load_players
from nfl_fantasy.matching import normalize_name
from nfl_fantasy.store import load_settings
from nfl_fantasy.valuation import value_board
from nfl_fantasy.vona import roster_cap, snake_picks, team_at_pick

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


def read_names(path: Path) -> list[str]:
    """Drafted players, one per line, in pick order."""
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def drafted_names(args, teams: int) -> tuple[list[str], list[int]]:
    """Names in pick order, and the slot that took each.

    With no live feed the slot has to be inferred from position in the file.
    That is sound as long as the file is complete and in order, which is the
    same assumption the draft board itself makes.
    """
    if args.draft_id:
        from watch import fetch, name_of
        picks = fetch(args.draft_id)
        return [name_of(p) for p in picks], [p.get("draft_slot") for p in picks]
    names = read_names(args.taken or Path(f"data/taken_{args.league}.txt"))
    return names, [team_at_pick(i, teams) for i in range(1, len(names) + 1)]


def play_out(forced, pool, mine, rosters, taken_positions, settings, slot,
             teams, rounds, start_pick, seed, by_position=False):
    """Finish the draft from here, taking `forced` at your next turn(s).

    `forced` is a player to take now, or a list of positions to take at your
    next picks in order. Anything not forced is chosen by the model.
    """
    rng = random.Random(seed)
    pool = list(pool)
    mine = list(mine)
    rosters = {t: list(v) for t, v in rosters.items()}
    taken_positions = list(taken_positions)
    my_picks = set(snake_picks(slot, teams, rounds))
    queue = list(forced) if by_position else [forced]

    for overall in range(start_pick, rounds * teams + 1):
        if not pool:
            break
        if overall in my_picks:
            choice = None
            if queue:
                want = queue.pop(0)
                if by_position:
                    counts = Counter(v.player.position for v in mine)
                    fits = [v for v in pool if v.player.position == want
                            and counts[want] < roster_cap(want, settings)]
                    if fits:
                        choice = max(fits, key=lambda v: v.vor)
                elif want in pool:
                    choice = want
            if choice is None:
                choice = strat_vona(pool, mine, settings, slot, teams, rounds,
                                    overall, taken_positions)
            mine.append(choice)
        else:
            team = team_at_pick(overall, teams)
            choice = opponent_pick(pool, rosters[team], settings, rng)
            rosters[team].append(choice)
        pool.remove(choice)
        taken_positions.append(choice.player.position)
    return lineup_points(mine, settings)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="sleeper")
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--draft-id", default=None,
                        help="Sleeper draft id; omit to read the taken file")
    parser.add_argument("--taken", type=Path, default=None)
    parser.add_argument("--runs", type=int, default=40)
    parser.add_argument("--candidates", type=int, default=10)
    parser.add_argument("--next", type=int, default=0, metavar="N",
                        help="sweep position sequences for your next N picks")
    parser.add_argument("--core", default="",
                        help="positions the sweep may use (default: what you can still roster)")
    parser.add_argument("--kdst-round", type=int, default=None,
                        help="round from which opponents will take a K or DST")
    parser.add_argument("--only", default="",
                        help="restrict candidates to these positions, e.g. 'DST WR'")
    args = parser.parse_args()

    if args.kdst_round:
        simulate.KDST_FROM_ROUND = args.kdst_round
    settings = load_settings(args.league)
    teams = settings.teams
    rounds = len([s for s in settings.roster_slots if s != "IR"])

    names, slots = drafted_names(args, teams)
    made = len(names)
    gone = {normalize_name(n) for n in names}
    board = value_board(settings, load_players(args.league))
    by_key = {normalize_name(v.player.name): v for v in board}
    pool = [v for v in board if normalize_name(v.player.name) not in gone]

    rosters: dict[int, list] = defaultdict(list)
    for name, owner in zip(names, slots, strict=False):
        val = by_key.get(normalize_name(name))
        if val:
            rosters[owner].append(val)
    mine = rosters[args.slot]
    taken_positions = [by_key[normalize_name(n)].player.position
                       for n in names if normalize_name(n) in by_key]

    upcoming = [p for p in snake_picks(args.slot, teams, rounds) if p > made]
    if not upcoming:
        print("draft complete")
        return 0

    unmatched = len(names) - len(taken_positions)
    source = "sleeper api" if args.draft_id else "taken file"
    print(f"{args.league}: {made} picks in ({source}), your next: {upcoming[:4]}")
    print(f"roster ({len(mine)}): "
          f"{', '.join(v.player.name for v in mine) or 'empty'}")
    if unmatched:
        print(f"  !! {unmatched} drafted name(s) did not match the board -- "
              f"they are still being offered to you")

    started = time.perf_counter()
    if args.next:
        return sweep(args, settings, pool, mine, rosters, taken_positions,
                     teams, rounds, made, upcoming, started)
    return rank(args, settings, pool, mine, rosters, taken_positions,
                teams, rounds, made, started)


def rank(args, settings, pool, mine, rosters, taken_positions, teams, rounds,
         made, started):
    """Which single player to take now."""
    counts = Counter(v.player.position for v in mine)
    room = [v for v in pool
            if counts[v.player.position] < roster_cap(v.player.position, settings)]
    wanted = set(args.only.split())
    if wanted:
        room = [v for v in room if v.player.position in wanted]
    shortlist = sorted(room, key=lambda v: -v.vor)[:args.candidates]

    scored = []
    for candidate in shortlist:
        results = [play_out(candidate, pool, mine, rosters, taken_positions,
                            settings, args.slot, teams, rounds, made + 1, seed)
                   for seed in range(args.runs)]
        scored.append((statistics.mean(results),
                       statistics.stdev(results) / (args.runs ** 0.5), candidate))
    scored.sort(reverse=True)
    best = scored[0][0]
    print()
    print(f"  {'take now':<24}{'pos':>4}{'pts':>8}{'final':>8}{'+/-':>6}{'vs best':>9}")
    for mean, err, candidate in scored:
        print(f"  {candidate.player.name:<24}{candidate.player.position:>4}"
              f"{candidate.points:>8.1f}{mean:>8.0f}{err:>6.1f}{mean - best:>9.0f}")
    print(f"\n  {len(shortlist) * args.runs} drafts in "
          f"{time.perf_counter() - started:.1f}s")
    return 0


def sweep(args, settings, pool, mine, rosters, taken_positions, teams, rounds,
          made, upcoming, started):
    """Which shape of picks to take over your next few turns."""
    counts = Counter(v.player.position for v in mine)
    core = tuple(args.core.split()) if args.core else tuple(
        p for p in POSITIONS
        if counts[p] < roster_cap(p, settings)
        and any(v.player.position == p for v in pool)
    )
    depth = args.next
    seqs = [s for s in itertools.product(core, repeat=depth)
            if all(counts[p] + Counter(s)[p] <= roster_cap(p, settings) for p in s)]

    scored = []
    for seq in seqs:
        results = [play_out(seq, pool, mine, rosters, taken_positions, settings,
                            args.slot, teams, rounds, made + 1, seed,
                            by_position=True)
                   for seed in range(args.runs)]
        scored.append((statistics.mean(results),
                       statistics.stdev(results) / (args.runs ** 0.5), seq))
    scored.sort(reverse=True)
    best = scored[0][0]

    picks = "  ".join(str(p) for p in upcoming[:depth])
    print(f"\n  {len(seqs)} sequences x {args.runs} runs over picks {picks}")
    print(f"  {'sequence':<22}{'final':>8}{'+/-':>6}{'vs best':>9}")
    for mean, err, seq in scored[:12]:
        print(f"  {' '.join(seq):<22}{mean:>8.0f}{err:>6.1f}{mean - best:>9.0f}")
    if len(scored) > 12:
        print("  ...")
        for mean, err, seq in scored[-3:]:
            print(f"  {' '.join(seq):<22}{mean:>8.0f}{err:>6.1f}{mean - best:>9.0f}")

    # What survives averaging is the position at each turn, not the exact
    # sequence -- individual sequences sit inside each other's error bars far
    # more often than they differ.
    print()
    print(f"  {'pick':>6}" + "".join(f"{p:>8}" for p in core))
    for i, pick in enumerate(upcoming[:depth]):
        cells = ""
        for position in core:
            at = [m for m, _, seq in scored if seq[i] == position]
            cells += f"{statistics.mean(at):>8.0f}" if at else f"{'-':>8}"
        print(f"  {pick:>6}{cells}")
    print(f"\n  {len(seqs) * args.runs} drafts in "
          f"{time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
