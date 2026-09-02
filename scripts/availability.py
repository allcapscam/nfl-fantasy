"""Who actually reaches each of your picks, across many simulated rooms.

The strategy comparison answers "what shape of roster wins". This answers the
question you have thirty seconds to solve on the clock: at pick 9 the board is
what it is, and the useful preparation is knowing which names survive to 9, to
16, and to 33 often enough to plan around -- and which ones never do.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import random

from simulate import STRATEGIES, opponent_pick

from nfl_fantasy.advisor import load_players
from nfl_fantasy.store import load_settings
from nfl_fantasy.valuation import value_board
from nfl_fantasy.vona import snake_picks, team_at_pick


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="sleeper")
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--runs", type=int, default=250)
    parser.add_argument("--picks", type=int, default=3)
    args = parser.parse_args()

    settings = load_settings(args.league)
    teams = settings.teams
    rounds = len(settings.roster_slots)
    board = value_board(settings, load_players(args.league))
    mine = snake_picks(args.slot, teams, rounds)[: args.picks]
    strategy = STRATEGIES["VONA (the model)"]

    available = {p: Counter() for p in mine}
    chosen = {p: Counter() for p in mine}

    for seed in range(args.runs):
        rng = random.Random(seed)
        pool = list(board)
        roster = []
        taken_positions = []
        rosters = defaultdict(list)
        for overall in range(1, max(mine) + 1):
            if overall in available:
                top = sorted(pool, key=lambda v: -v.vor)[:12]
                for v in top:
                    available[overall][v.player.name] += 1
            if overall in mine:
                pick = strategy(pool, roster, settings, args.slot, teams,
                                rounds, overall, taken_positions)
                roster.append(pick)
                chosen[overall][pick.player.name] += 1
            else:
                team = team_at_pick(overall, teams)
                pick = opponent_pick(pool, rosters[team], settings, rng)
                rosters[team].append(pick)
            pool.remove(pick)
            taken_positions.append(pick.player.position)

    values = {v.player.name: v for v in board}
    for overall in mine:
        print(f"\npick {overall} -- reaches you in {args.runs} rooms")
        print(f"  {'player':<26}{'pos':>4}{'avail':>8}{'taken':>8}")
        for name, count in available[overall].most_common(10):
            took = chosen[overall][name]
            print(f"  {name:<26}{values[name].player.position:>4}"
                  f"{count / args.runs:>7.0%}{took / args.runs:>8.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
