"""Simulate only what is left of the draft, to rank the pick in front of you.

The full sweep replays 192 picks from an empty board to score a whole strategy.
Mid-draft that is almost all wasted work: the first sixty picks are settled
facts, and the question is not "what shape of roster wins" but "which of these
five names should I take right now".

So this starts from the live board, forces each candidate as your next pick,
plays the remainder out with the model making your later picks, and compares
the starting lineups that result. A dozen candidates over a hundred remaining
picks runs in seconds rather than minutes -- fast enough to use on the clock.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import simulate
from simulate import lineup_points, opponent_pick, strat_vona
from watch import fetch, name_of

from nfl_fantasy.advisor import load_players
from nfl_fantasy.matching import normalize_name
from nfl_fantasy.store import load_settings
from nfl_fantasy.valuation import value_board
from nfl_fantasy.vona import roster_cap, snake_picks, team_at_pick


def play_out(first, pool, mine, rosters, taken_positions, settings, slot,
             teams, rounds, start_pick, seed):
    """Finish the draft from here, taking `first` at your next turn."""
    rng = random.Random(seed)
    pool = list(pool)
    mine = list(mine)
    rosters = {t: list(v) for t, v in rosters.items()}
    taken_positions = list(taken_positions)
    my_picks = set(snake_picks(slot, teams, rounds))
    forced = first

    for overall in range(start_pick, rounds * teams + 1):
        if not pool:
            break
        if overall in my_picks:
            if forced is not None:
                choice = forced
                forced = None
                if choice not in pool:
                    continue
            else:
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
    parser.add_argument("--draft-id", default="1389720881625841665")
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--runs", type=int, default=40)
    parser.add_argument("--candidates", type=int, default=10)
    parser.add_argument("--kdst-round", type=int, default=None,
                        help="round from which opponents will take a K or DST")
    parser.add_argument("--only", default="",
                        help="restrict to these positions, e.g. 'DST WR'")
    args = parser.parse_args()

    if args.kdst_round:
        simulate.KDST_FROM_ROUND = args.kdst_round
    settings = load_settings(args.league)
    teams = settings.teams
    rounds = len([s for s in settings.roster_slots if s != "IR"])
    picks = fetch(args.draft_id)
    made = len(picks)
    gone = {normalize_name(name_of(p)) for p in picks}

    board = value_board(settings, load_players(args.league))
    pool = [v for v in board if normalize_name(v.player.name) not in gone]
    by_key = {normalize_name(v.player.name): v for v in board}

    rosters: dict[int, list] = defaultdict(list)
    for pick in picks:
        val = by_key.get(normalize_name(name_of(pick)))
        if val:
            rosters[pick.get("draft_slot")].append(val)
    mine = rosters[args.slot]
    taken_positions = [by_key[normalize_name(name_of(p))].player.position
                       for p in picks if normalize_name(name_of(p)) in by_key]

    target = next((p for p in snake_picks(args.slot, teams, rounds) if p > made), None)
    if target is None:
        print("draft complete")
        return 0

    counts = Counter(v.player.position for v in mine)
    room = [v for v in pool
            if counts[v.player.position] < roster_cap(v.player.position, settings)]
    wanted = set(args.only.split())
    if wanted:
        room = [v for v in room if v.player.position in wanted]
    shortlist = sorted(room, key=lambda v: -v.vor)[:args.candidates]

    print(f"picks made {made}, your pick {target}, "
          f"{len(pool)} left on the board, {args.runs} runs each")
    print(f"roster: {', '.join(v.player.name for v in mine) or 'empty'}")
    print()
    print(f"  {'take now':<24}{'pos':>4}{'final pts':>11}{'+/-':>7}")

    scored = []
    for candidate in shortlist:
        results = [play_out(candidate, pool, mine, rosters, taken_positions,
                            settings, args.slot, teams, rounds, made + 1, seed)
                   for seed in range(args.runs)]
        scored.append((statistics.mean(results),
                       statistics.stdev(results) / (args.runs ** 0.5), candidate))
    scored.sort(reverse=True)
    best = scored[0][0]
    for mean, err, candidate in scored:
        print(f"  {candidate.player.name:<24}{candidate.player.position:>4}"
              f"{mean:>11.0f}{err:>7.0f}   {mean - best:+.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
