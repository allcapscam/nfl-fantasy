"""One-shot draft-day advice, built for speed.

A live draft gives you about ninety seconds. Assembling the analysis from
scratch each turn costs several round trips and blows the clock -- which is
exactly how a pick got autodrafted. This takes the board straight off the draft
room in the shorthand Yahoo renders ("L. McConkey|WR") and prints the
shortlist, so a turn is two calls: read the room, run this.

Usage:
  uv run python scripts/live_advise.py --pick 56 --slot 5 --teams 10 \
      --roster "Drake Maye;Malik Nabers;Ladd McConkey" \
      --avail "J. Jacobs|RB T. Etienne|RB T. Kraft|TE"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nfl_fantasy.advisor import load_players, normalize
from nfl_fantasy.store import load_settings
from nfl_fantasy.upside import describe, load_history, upside_multiplier
from nfl_fantasy.valuation import value_board
from nfl_fantasy.vona import (
    blend_runs,
    candidates,
    diversify,
    runs_from_adp,
    runs_from_needs,
    snake_picks,
)

SUFFIXES = (" Jr", " Sr", " II", " III", ".")


def resolve(board, shorthand: str):
    """Match Yahoo's 'L. McConkey|WR' against a full name on our board."""
    if "|" not in shorthand:
        return None
    abbrev, position = shorthand.rsplit("|", 1)
    abbrev = abbrev.strip()
    if ". " not in abbrev:
        return None
    initial, last = abbrev.split(". ", 1)
    for suffix in SUFFIXES:
        last = last.removesuffix(suffix)
    last = last.strip().lower()
    for valuation in board:
        player = valuation.player
        if player.position != position.strip():
            continue
        if player.name.split()[0][0].lower() == initial[0].lower() and last in player.name.lower():
            return valuation
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="yahoo1")
    parser.add_argument("--pick", type=int, required=True)
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--teams", type=int, default=None)
    parser.add_argument("--roster", default="", help="Your players, ; separated.")
    parser.add_argument("--avail", default="", help="Yahoo shorthand, space separated.")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()

    settings = load_settings(args.league)
    teams = args.teams or settings.teams
    players = load_players(args.league)
    board = value_board(settings, players)
    history = load_history(Path(f"data/projections/{args.league}_history.csv"))

    # Names contain spaces, so splitting on whitespace tears them apart. Pull
    # out every "X. Surname|POS" token instead, whatever separates them.
    tokens = re.findall(r"[A-Z]\.\s?[A-Za-z'\-\.]+(?:\s(?:Jr|Sr|II|III))?\s*\|\s*"
                        r"(?:QB|RB|WR|TE|K|DEF|DST)", args.avail)
    available = [v for v in (resolve(board, t) for t in tokens) if v]
    if not available:
        print("No players matched the board text.")
        return 1

    mine = [n.strip() for n in args.roster.split(";") if n.strip()]
    by_key = {v.player.id: v for v in board}
    roster_counts: dict[str, int] = {}
    for name in mine:
        v = by_key.get(normalize(name))
        if v:
            roster_counts[v.player.position] = roster_counts.get(v.player.position, 0) + 1

    rounds = len([s for s in settings.roster_slots if s != "IR"])
    picks = snake_picks(args.slot, teams, rounds)
    following = next((p for p in picks if p > args.pick), None)
    round_number = (args.pick - 1) // teams + 1

    adp = {v.player.name: v.player.adp for v in board if v.player.adp}
    positions = {v.player.name: v.player.position for v in board}
    prior = runs_from_adp(adp, positions, args.pick, following or args.pick)
    # Reconstruct the room from ADP order: whoever is gone is, near enough, the
    # top of the ADP board. Good enough for the need model.
    gone = [
        v.player.position
        for v in sorted((v for v in board if v.player.adp), key=lambda v: v.player.adp)
        [: args.pick - 1]
    ]
    needs = runs_from_needs(
        settings, gone, args.pick, following or args.pick, args.slot, teams=teams
    )
    runs = blend_runs(prior, needs, round_number)

    ranked = candidates(available, runs, settings, roster_counts)
    for candidate in ranked:
        key = candidate.valuation.player.id
        candidate.upside = upside_multiplier(key, history, round_number)
        candidate.upside_note = describe(key, history)
    ranked.sort(key=lambda c: c.cost_of_waiting, reverse=True)
    shortlist = diversify(ranked, count=args.count, min_positions=2)

    have = ", ".join(f"{p}{n}" for p, n in sorted(roster_counts.items()))
    print(f"pick {args.pick} (round {round_number}) -> next {following}   have: {have}")
    print("expect gone: " + "  ".join(
        f"{p} {runs[p]:.1f}" for p in ("QB", "RB", "WR", "TE") if runs[p] > 0.05))
    print(f"\n{'#':<3}{'player':<24}{'pos':<5}{'VOR':>7}{'if wait':>8}{'cost':>7}  note")
    for index, candidate in enumerate(shortlist, start=1):
        v = candidate.valuation
        note = candidate.upside_note or ""
        if v.games and v.games < 16:
            note = (note + f" {v.games}gm").strip()
        print(f"{index:<3}{v.player.name:<24}{candidate.position:<5}{v.vor:>7.1f}"
              f"{candidate.expected_next:>8.1f}{candidate.cost_of_waiting:>7.1f}  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
