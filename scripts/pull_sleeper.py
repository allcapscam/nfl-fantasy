"""Build a league's projection files straight from Sleeper's public API.

Sleeper is the one platform that needs no browser at all. Its API is public and
read-only, and it carries everything the model wants in one place: projected
points under the league's scoring, projected games played, average draft
position, bye weeks, and last season's actuals for the upside flag.

That matters beyond convenience. On the other two platforms the board has to be
read off a rendered page, which cost a pick to the clock in one draft and left
the model running on a partial board in another. Here the same data arrives in
three HTTP calls.

Usage:
  uv run python scripts/pull_sleeper.py --league sleeper --scoring half_ppr
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nfl_fantasy.platforms.sleeper import SleeperAdapter

BASE = "https://api.sleeper.app"
POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}
POSITION_MAP = {"DEF": "DST"}

#: Sleeper projects an eighteen-game season; the valuation model reasons in
#: sixteen. Clamping keeps a full-season player reading as full-season rather
#: than as someone with two spare weeks to backfill.
FULL_SEASON = 16

#: Sleeper reports gp=1 for every defence -- its projection is a season total,
#: not a one-game sample. Taken literally the games adjustment backfills fifteen
#: missing weeks at replacement rate, which doubled every defence's value and
#: floated the Rams into the first round. A defence never misses a week: the
#: unit plays whenever the team does.
ALWAYS_FULL_SEASON = {"DEF"}  # Sleeper's raw code, before POSITION_MAP


def fetch(url: str, timeout: float = 90.0):
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="sleeper")
    parser.add_argument("--league-id", default="1389720881625841664")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--scoring", default="half_ppr",
                        choices=["std", "half_ppr", "ppr"])
    parser.add_argument("--out", type=Path, default=Path("data/projections"))
    args = parser.parse_args()

    points_key = f"pts_{args.scoring}"
    adp_key = f"adp_{args.scoring}"

    players = SleeperAdapter(key=args.league, league_id=args.league_id).all_players()
    projections = fetch(f"{BASE}/v1/projections/nfl/regular/{args.season}")
    try:
        prior = fetch(f"{BASE}/v1/stats/nfl/regular/{int(args.season) - 1}")
    except httpx.HTTPError:
        prior = {}

    rows = []
    for pid, stats in projections.items():
        record = players.get(pid)
        if not record:
            continue
        position = record.get("position")
        if position not in POSITIONS:
            continue
        points = stats.get(points_key)
        if points is None:
            continue
        games = stats.get("gp")
        adp = stats.get(adp_key)
        rows.append({
            "name": (record.get("full_name")
                     or f"{record.get('first_name','')} {record.get('last_name','')}".strip()),
            "team": record.get("team") or "",
            "position": POSITION_MAP.get(position, position),
            "points": round(float(points), 1),
            "games": (FULL_SEASON
                      if position in ALWAYS_FULL_SEASON or not games
                      else min(int(float(games)), FULL_SEASON)),
            "bye": record.get("bye_week") or "",
            # Sleeper's ADP uses 999 to mean "essentially undrafted".
            "adp": round(float(adp), 1) if adp and float(adp) < 900 else "",
            "prior": round(float((prior.get(pid) or {}).get(points_key) or 0), 1),
        })

    rows.sort(key=lambda r: -r["points"])
    args.out.mkdir(parents=True, exist_ok=True)

    def write(name: str, header: list[str], build) -> Path:
        path = args.out / name
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            for row in rows:
                built = build(row)
                if built is not None:
                    writer.writerow(built)
        return path

    write(f"{args.league}.csv", ["name", "team", "position", "games", "bye", "points"],
          lambda r: [r["name"], r["team"], r["position"], r["games"], r["bye"], r["points"]])
    write(f"{args.league}_adp.csv", ["name", "position", "adp"],
          lambda r: [r["name"], r["position"], r["adp"]] if r["adp"] != "" else None)
    # Sleeper ships no bye weeks at all, so this file is built elsewhere (from
    # ESPN's schedule endpoint). Writing an empty one over it silently disables
    # the bye-crowding penalty, which is how a real draft ended up being offered
    # a kicker into a week that already had four starters out.
    byes = args.out / f"{args.league}_byes.csv"
    if any(r["bye"] != "" for r in rows) or not byes.exists():
        write(f"{args.league}_byes.csv", ["name", "bye"],
              lambda r: [r["name"], r["bye"]] if r["bye"] != "" else None)
    else:
        print(f"  kept existing {byes} (feed carried no byes)")
    write(f"{args.league}_history.csv",
          ["name", "position", "prior_games", "prior_points"],
          lambda r: [r["name"], r["position"], 0 if r["prior"] < 60 else FULL_SEASON,
                     r["prior"]])

    with_adp = sum(1 for r in rows if r["adp"] != "")
    with_bye = sum(1 for r in rows if r["bye"] != "")
    unproven = sum(1 for r in rows if r["prior"] < 60)
    print(f"{len(rows)} players -> {args.out}/{args.league}*.csv")
    print(f"  {with_adp} with ADP, {with_bye} with byes, {unproven} with a thin 2025")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
