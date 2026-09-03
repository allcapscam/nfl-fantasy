"""Read the live Sleeper draft and say what to take, in one command.

Everything comes from the public API: who has been picked, which of them are
yours, and what is left. No screenshot, no pasted board. On a ninety-second
clock that difference is the whole thing -- a previous draft lost a pick to
autodraft because reading the screen took six round trips.

Usage:
  uv run python scripts/live.py --slot 9
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nfl_fantasy.advisor import advise, load_players
from nfl_fantasy.store import load_settings
from nfl_fantasy.vona import next_pick_after, snake_picks

BASE = "https://api.sleeper.app/v1/draft"


def name_of(pick: dict) -> str:
    meta = pick.get("metadata") or {}
    return f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="sleeper")
    parser.add_argument("--draft-id", default="1389720881625841665")
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--shortlist", type=int, default=6)
    args = parser.parse_args()

    picks = httpx.get(f"{BASE}/{args.draft_id}/picks", timeout=30).json()
    taken = [name_of(p) for p in picks]
    mine = [name_of(p) for p in picks if p.get("draft_slot") == args.slot]

    settings = load_settings(args.league)
    teams = settings.teams
    rounds = len([s for s in settings.roster_slots if s != "IR"])
    on_clock = len(picks) + 1
    my_picks = snake_picks(args.slot, teams, rounds)
    following = next_pick_after(on_clock, args.slot, teams, rounds)

    status = "YOURS -- on the clock" if on_clock in my_picks else "not yours"
    print(f"pick {on_clock} (round {(on_clock - 1) // teams + 1}) -- {status}")
    print(f"your picks: {my_picks[:8]}")
    if following:
        print(f"next turn: pick {following} ({following - on_clock} picks away)")
    print(f"roster ({len(mine)}): {', '.join(mine) or 'empty'}")

    advice = advise(settings, load_players(args.league), slot=args.slot,
                    taken=taken, my_roster=mine, rounds=rounds,
                    shortlist_size=args.shortlist, draft_teams=teams)

    print()
    print("expected gone before your next pick: " + "  ".join(
        f"{p} {n:.1f}" for p, n in advice.runs.items() if n))
    for warning in advice.warnings:
        print(f"  !! {warning}")

    print()
    header = f"  {'player':<24}{'pos':>4}{'pts':>8}{'vor':>8}{'cost':>8}"
    print(header)
    for candidate in advice.shortlist:
        val = candidate.valuation
        notes = [n for n in (candidate.upside_note, candidate.bye_note,
                             candidate.market_note) if n]
        bench = "" if candidate.starts else "  (bench)"
        print(f"  {val.player.name:<24}{val.player.position:>4}"
              f"{val.points:>8.1f}{val.vor:>8.1f}"
              f"{candidate.cost_of_waiting:>8.1f}{bench}")
        for note in notes:
            print(f"      - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
