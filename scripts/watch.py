"""Wait until your pick is close, then print the board.

Polling by hand between picks burns the clock and the attention you need for
the pick itself. This blocks until the draft reaches a threshold and then dumps
everything worth seeing in one shot: what went since you last looked, what each
team still has to fill, and the live tiers with the cliffs marked.

Usage:
  uv run python scripts/watch.py --slot 9 --within 5
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nfl_fantasy.advisor import load_players
from nfl_fantasy.matching import normalize_name
from nfl_fantasy.settings import LeagueSettings
from nfl_fantasy.store import load_settings
from nfl_fantasy.valuation import value_board
from nfl_fantasy.vona import snake_picks, team_needs

BASE = "https://api.sleeper.app/v1/draft"
TRACKED = ("QB", "RB", "WR", "TE", "K", "DST")

#: Sleeper's picks feed lags the room by a few seconds, sometimes by a couple of
#: picks. Polling faster than this does not make it fresher, it just burns calls.
POLL_SECONDS = 8


def name_of(pick: dict) -> str:
    meta = pick.get("metadata") or {}
    return f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()


def fetch(draft_id: str) -> list[dict]:
    return httpx.get(f"{BASE}/{draft_id}/picks", timeout=30).json()


def report(picks: list[dict], settings: LeagueSettings, slot: int,
           since: int, target: int) -> None:
    made = len(picks)
    print(f"picks made: {made}   on the clock: {made + 1}   "
          f"your pick: {target} ({target - made - 1} away)")

    if made > since:
        print()
        print(f"  since pick {since}:")
        for pick in picks[since:]:
            meta = pick.get("metadata") or {}
            mine = "  <-- you" if pick.get("draft_slot") == slot else ""
            print(f"    {pick['pick_no']:>3}  {name_of(pick):<24}"
                  f"({meta.get('position')}){mine}")

    rosters: dict[int, Counter] = defaultdict(Counter)
    for pick in picks:
        rosters[pick.get("draft_slot")][(pick.get("metadata") or {})
                                        .get("position")] += 1
    unfilled: Counter = Counter()
    for team in range(1, settings.teams + 1):
        for position, need in team_needs(settings, rosters[team]).items():
            unfilled[position] += need
    print()
    print("  starting slots still unfilled across the room:")
    print("    " + "   ".join(f"{p} {unfilled[p]:.0f}" for p in TRACKED))

    gone = {normalize_name(name_of(p)) for p in picks}
    board = [v for v in value_board(settings, load_players(settings.key))
             if normalize_name(v.player.name) not in gone]
    print()
    for position in ("RB", "WR", "QB", "TE", "K", "DST"):
        at = sorted((v for v in board if v.player.position == position),
                    key=lambda v: -v.points)[:6]
        if not at:
            continue
        print(f"  {position}")
        for i, v in enumerate(at):
            adp = v.player.adp or 0.0
            # A player already past his ADP is the one most likely to go next --
            # the run estimator counts ADP falling inside the window and so
            # misses him entirely. Two were lost to this in one draft.
            flags = "  PAST ADP" if adp and made + 1 > adp + 3 else ""
            # Mark the drop to the next man, which is what waiting actually costs.
            if i + 1 < len(at):
                gap = v.vor - at[i + 1].vor
                flags += "  <-- cliff" if gap >= 8 else ""
            print(f"    {v.player.name:<22}{v.points:>7.1f}  vor {v.vor:>5.1f}  "
                  f"adp {adp:>5.1f}  bye {v.player.bye_week or '-'!s:>3}{flags}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="sleeper")
    parser.add_argument("--draft-id", default="1389720881625841665")
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--within", type=int, default=5,
                        help="report once your pick is this many picks away")
    parser.add_argument("--since", type=int, default=0)
    # Sleeper's feed can sit a couple of picks behind the room. Without a floor
    # the watcher reports against a board that has not registered your own last
    # pick yet, and answers for a turn that is already over.
    parser.add_argument("--after", type=int, default=0,
                        help="do not report until this many picks are in")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    settings = load_settings(args.league)
    rounds = len([s for s in settings.roster_slots if s != "IR"])
    my_picks = snake_picks(args.slot, settings.teams, rounds)

    deadline = time.monotonic() + args.timeout
    while True:
        picks = fetch(args.draft_id)
        made = len(picks)
        target = next((p for p in my_picks if p > made), None)
        if target is None:
            print("draft complete")
            return 0
        ready = made >= args.after and target - made - 1 <= args.within
        if ready or time.monotonic() > deadline:
            report(picks, settings, args.slot, args.since, target)
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
