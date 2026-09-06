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

from nfl_fantasy.leagues import LeagueRegistry
from nfl_fantasy.matching import TEAM_ALIASES
from nfl_fantasy.platforms.sleeper import SleeperAdapter
from nfl_fantasy.scoring import (
    compare_tables,
    reconcile_by_position,
    score_line,
    unused_keys,
)

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


#: Sleeper ships no bye weeks at all -- the field is empty for every one of its
#: twelve thousand players. ESPN's season schedule is public, needs no key, and
#: carries a bye week per pro team, so byes are mapped from there by team code.
#: Doing this by hand once cost a draft its bye-crowding penalty entirely; it
#: belongs in the pull.
SCHEDULE = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"
            "/{season}?view=proTeamSchedules_wl")


def fetch(url: str, timeout: float = 90.0):
    response = httpx.get(url, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    return response.json()


def team_byes(season: str) -> dict[str, int]:
    """Bye week per team code, from ESPN's public schedule."""
    try:
        data = fetch(SCHEDULE.format(season=season), timeout=60.0)
    except httpx.HTTPError:
        return {}
    byes = {}
    for team in data.get("settings", {}).get("proTeams", []):
        code = (team.get("abbrev") or "").upper()
        if code and team.get("byeWeek"):
            byes[TEAM_ALIASES.get(code, code)] = team["byeWeek"]
    return byes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="sleeper")
    parser.add_argument("--league-id", default="1389720881625841664")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--scoring", default="half_ppr",
                        choices=["std", "half_ppr", "ppr"])
    parser.add_argument("--out", type=Path, default=Path("data/projections"))
    parser.add_argument(
        "--score-with", default=None, metavar="LEAGUE",
        help="Score projections with this league's own scoring_table from "
             "leagues.yaml, instead of taking Sleeper's precomputed total. "
             "Sleeper's total is Sleeper's idea of the format, not your "
             "league's, and nothing says so when they differ.")
    parser.add_argument(
        "--registry", type=Path, default=Path("leagues.yaml"),
        help="Where --score-with looks for the league.")
    parser.add_argument(
        "--adp", choices=["std", "half_ppr", "ppr", "2qb", "dynasty"], default=None,
        help="Which ADP board to read. Defaults to --scoring. Use 2qb for a "
             "superflex league: single-QB ADP has the QB1 going in round three, "
             "and the whole market prior is wrong from the first pick.")
    args = parser.parse_args()

    points_key = f"pts_{args.scoring}"
    adp_key = f"adp_{args.adp or args.scoring}"

    table: dict[str, float] = {}
    if args.score_with:
        ref = LeagueRegistry.load(args.registry).get(args.score_with)
        table = dict((ref.manual.scoring_table if ref.manual else {}) or {})
        if not table:
            print(f"{args.score_with!r} has no scoring_table in {args.registry}. "
                  "Add one, or drop --score-with to use Sleeper's own total.")
            return 1

    players = SleeperAdapter(key=args.league, league_id=args.league_id).all_players()
    projections = fetch(f"{BASE}/v1/projections/nfl/regular/{args.season}")
    try:
        prior = fetch(f"{BASE}/v1/stats/nfl/regular/{int(args.season) - 1}")
    except httpx.HTTPError:
        prior = {}

    byes = team_byes(args.season)
    entries: list[tuple[str, dict, dict]] = []
    for pid, stats in projections.items():
        record = players.get(pid)
        if not record:
            continue
        if record.get("position") not in POSITIONS:
            continue
        if stats.get(points_key) is None:
            continue
        entries.append((pid, record, stats))

    lines: list[tuple[str, str, dict]] = [
        (record.get("full_name") or "",
         POSITION_MAP.get(record["position"], record["position"]),
         stats)
        for _pid, record, stats in entries
    ]

    # Scoring from raw stats is only sound if the stat keys are the ones the
    # feed uses. A misspelled key contributes nothing and silently shrinks every
    # total that depended on it, so prove the names against arithmetic Sleeper
    # has already done rather than trusting them.
    #
    # Proved per position, because the feed is uneven and one number over the
    # whole board cannot tell the two failures apart. Sleeper's defensive
    # projection carries sacks, interceptions, fumble recoveries and blocked
    # kicks and nothing else -- no points-allowed bucket, no defensive
    # touchdown -- so no table reproduces its defensive total, while every skill
    # player reconciles exactly. Judged together that is one refusal and no
    # board at all; judged per position it is four positions proven and one the
    # feed cannot support.
    scorable: set[str] = set()
    if table:
        checks = reconcile_by_position(lines)
        print("  reproducing Sleeper's own "
              f"{args.scoring} total from its stat keys, position by position")
        print(f"  {'pos':>5}{'players':>9}{'agree':>8}{'mean':>8}{'worst':>8}"
              f"  verdict")
        for position, check in checks.items():
            print(f"  {position:>5}{check.checked:>9}{check.agreement:>8.1%}"
                  f"{check.mean_error:>8.2f}{check.worst_error:>8.2f}"
                  f"  {'keys proven' if check.ok() else 'NOT REPRODUCIBLE'}")
        scorable = {p for p, c in checks.items() if c.ok()}
        if not scorable:
            print("\n  Sleeper's own total could not be reproduced for any "
                  "position, so the stat keys are wrong and the league table "
                  "cannot be trusted either.")
            print("  Nothing written. Re-run without --score-with to use "
                  "Sleeper's precomputed total, and report the keys above.")
            return 1
        fell_back = sorted(set(checks) - scorable)
        if fell_back:
            print(f"\n  {', '.join(fell_back)} keep Sleeper's precomputed "
                  f"{args.scoring} total: the projected line carries none of "
                  "the stats those rules pay out, so no table -- Sleeper's own "
                  "included -- can score it from the feed. Those positions are "
                  "scored under Sleeper's idea of the format, not your "
                  "league's.")
        print()

    rows = []
    for pid, record, stats in entries:
        position = record["position"]
        mapped = POSITION_MAP.get(position, position)
        points = stats.get(points_key)
        if table and mapped in scorable:
            points = score_line(stats, table)
        games = stats.get("gp")
        adp = stats.get(adp_key)
        rows.append({
            "name": (record.get("full_name")
                     or f"{record.get('first_name','')} {record.get('last_name','')}".strip()),
            "team": record.get("team") or "",
            "position": mapped,
            "points": round(float(points), 1),
            "games": (FULL_SEASON
                      if position in ALWAYS_FULL_SEASON or not games
                      else min(int(float(games)), FULL_SEASON)),
            "bye": (record.get("bye_week")
                    or byes.get(TEAM_ALIASES.get((record.get("team") or "").upper(),
                                                 (record.get("team") or "").upper()), "")),
            # Sleeper's ADP uses 999 to mean "essentially undrafted".
            "adp": round(float(adp), 1) if adp and float(adp) < 900 else "",
            "prior": round(float((prior.get(pid) or {}).get(points_key) or 0), 1),
        })

    if table:
        # Only the positions actually scored with the league's table -- the ones
        # that fell back are being scored by Sleeper, so its rules are not
        # "inert" for them and the difference is not the league's to read.
        scored = [(n, p, st) for n, p, st in lines if p in scorable]
        inert = unused_keys([(n, st) for n, _p, st in scored], table)
        if inert:
            print(f"  {len(inert)} of your league's rules are inert across "
                  f"{', '.join(sorted(scorable))} -- Sleeper projects no such "
                  "stat, so they never pay out:")
            print("    " + ", ".join(inert))
            print()
        print(f"  your scoring vs Sleeper's {args.scoring}, by position")
        print(f"  {'pos':>5}{'players':>9}{'mean diff':>12}{'largest':>10}")
        for position, (count, mean, worst) in compare_tables(scored, table).items():
            print(f"  {position:>5}{count:>9}{mean:>+12.1f}{worst:>+10.1f}")
        print()

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
    # If the schedule lookup failed, keep whatever byes are already on disk.
    # Writing an empty file over them silently disables the bye-crowding
    # penalty, which is how a real draft came to be offered a kicker into a
    # week that already had four starters out.
    byes_path = args.out / f"{args.league}_byes.csv"
    if any(r["bye"] != "" for r in rows) or not byes_path.exists():
        write(f"{args.league}_byes.csv", ["name", "bye"],
              lambda r: [r["name"], r["bye"]] if r["bye"] != "" else None)
    else:
        print(f"  kept existing {byes_path} (schedule lookup failed)")
    write(f"{args.league}_history.csv",
          ["name", "position", "prior_games", "prior_points"],
          lambda r: [r["name"], r["position"], 0 if r["prior"] < 60 else FULL_SEASON,
                     r["prior"]])

    with_adp = sum(1 for r in rows if r["adp"] != "")
    print(f"  ADP board: {adp_key}")
    with_bye = sum(1 for r in rows if r["bye"] != "")
    unproven = sum(1 for r in rows if r["prior"] < 60)
    print(f"{len(rows)} players -> {args.out}/{args.league}*.csv")
    print(f"  {with_adp} with ADP, {with_bye} with byes, {unproven} with a thin 2025")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
