"""What the board still offers at each of your picks, position by position.

This is the "when do I take a quarterback" question asked properly. Waiting on
a position costs you the difference between the best one available now and the
best one still there at your next pick -- so the useful table is not a ranking
but a decay curve, one row per pick, showing what survives.

Read down a column: a position that barely falls can wait, and a position that
drops a tier between two of your picks cannot.
"""

from __future__ import annotations

import argparse
import itertools
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from simulate import STRATEGIES, opponent_pick

from nfl_fantasy.advisor import load_players
from nfl_fantasy.store import load_settings
from nfl_fantasy.valuation import value_board
from nfl_fantasy.vona import snake_picks, team_at_pick

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="sleeper")
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--runs", type=int, default=60)
    args = parser.parse_args()

    settings = load_settings(args.league)
    teams = settings.teams
    rounds = len(settings.roster_slots)
    board = value_board(settings, load_players(args.league))
    mine = snake_picks(args.slot, teams, rounds)
    strategy = STRATEGIES["VONA (the model)"]

    best_left = {p: defaultdict(list) for p in mine}
    took = defaultdict(list)

    for seed in range(args.runs):
        rng = random.Random(seed)
        pool = list(board)
        roster, taken_positions = [], []
        rosters = defaultdict(list)
        for overall in range(1, rounds * teams + 1):
            if overall in best_left:
                for pos in POSITIONS:
                    at = [v.points for v in pool if v.player.position == pos]
                    if at:
                        best_left[overall][pos].append(max(at))
            if overall in mine:
                pick = strategy(pool, roster, settings, args.slot, teams,
                                rounds, overall, taken_positions)
                roster.append(pick)
                took[overall].append(pick.player.position)
            else:
                team = team_at_pick(overall, teams)
                pick = opponent_pick(pool, rosters[team], settings, rng)
                rosters[team].append(pick)
            pool.remove(pick)
            taken_positions.append(pick.player.position)

    print(f"best available at each of your picks, mean of {args.runs} rooms\n")
    head = "".join(f"{p:>7}" for p in POSITIONS)
    print(f"  {'rd':>3}{'pick':>6}{head}   model takes")
    for rnd, overall in enumerate(mine, start=1):
        cells = "".join(
            f"{statistics.mean(best_left[overall][p]):>7.0f}"
            if best_left[overall][p] else f"{'-':>7}"
            for p in POSITIONS)
        common = max(set(took[overall]), key=took[overall].count)
        share = took[overall].count(common) / len(took[overall])
        print(f"  {rnd:>3}{overall:>6}{cells}   {common} ({share:.0%})")

    print("\n  cost of waiting one turn (points lost off the top of each position)\n")
    print(f"  {'rd':>3}{head}")
    for rnd, (a, b) in enumerate(itertools.pairwise(mine), start=1):
        cells = ""
        for p in POSITIONS:
            if best_left[a][p] and best_left[b][p]:
                drop = statistics.mean(best_left[a][p]) - statistics.mean(best_left[b][p])
                cells += f"{drop:>7.0f}"
            else:
                cells += f"{'-':>7}"
        print(f"  {rnd:>3}{cells}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
