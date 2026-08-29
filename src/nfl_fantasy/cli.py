"""Command line entry point."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from nfl_fantasy.draft import rank_board, rank_queue
from nfl_fantasy.leagues import LeagueRef, LeagueRegistry
from nfl_fantasy.matching import apply_rankings
from nfl_fantasy.platforms import yahoo_auth
from nfl_fantasy.platforms.base import Player
from nfl_fantasy.platforms.sleeper import SleeperAdapter
from nfl_fantasy.platforms.yahoo import YahooAdapter
from nfl_fantasy.sources.csv_source import CsvRankingSource
from nfl_fantasy.sources.fantasypros import FantasyProsSource
from nfl_fantasy.store import load_rankings, load_settings, save_rankings, save_settings
from nfl_fantasy.strategy import Strategy

console = Console()
DEFAULT_REGISTRY = Path("leagues.yaml")


def build_adapter(ref: LeagueRef):
    """Construct the adapter for a league. Credentials come from the environment."""
    if ref.platform == "sleeper":
        return SleeperAdapter(
            key=ref.key,
            league_id=ref.league_id,
            draft_id=ref.draft_id,
            user_id=os.environ.get("SLEEPER_USER_ID"),
        )
    if ref.platform == "yahoo":
        return YahooAdapter(key=ref.key, league_id=ref.league_id)
    raise NotImplementedError(
        f"No adapter for {ref.platform!r} yet. Sleeper and Yahoo are "
        "implemented; ESPN is next."
    )


def cmd_auth_yahoo() -> int:
    """Walk the user through Yahoo's out-of-band authorization once."""
    client_id, _ = yahoo_auth.credentials()
    console.print("\n1. Open this URL and approve access:\n")
    console.print(f"   [cyan]{yahoo_auth.authorize_url(client_id)}[/cyan]\n")
    console.print("2. Yahoo shows you a code. Paste it below.\n")
    code = input("   Code: ").strip()
    if not code:
        console.print("[red]No code entered.[/red]")
        return 1

    token = yahoo_auth.exchange_code(code)
    path = token.save()
    console.print(f"[green]Authorized.[/green] Token saved to {path} "
                  "(gitignored, refreshes automatically).")
    return 0


def cmd_leagues(registry: LeagueRegistry) -> int:
    table = Table("League", "Platform", "League ID", "Strategy", "Synced settings")
    for ref in registry.leagues:
        try:
            summary = load_settings(ref.key).describe()
        except FileNotFoundError:
            summary = "[dim]not synced[/dim]"
        table.add_row(ref.key, ref.platform, ref.league_id, str(ref.strategy), summary)
    console.print(table)
    return 0


def cmd_sync(registry: LeagueRegistry, only: str | None) -> int:
    refs = [registry.get(only)] if only else registry.active
    failures = 0
    for ref in refs:
        source = "synced"
        try:
            settings = build_adapter(ref).fetch_settings()
        except Exception as error:  # noqa: BLE001 - report and continue to the next league
            # Hand-entered rules are the fallback, not the default: a league
            # that can be read from its platform can never drift out of date.
            if ref.manual is None:
                console.print(f"[red]{ref.key}:[/red] {error}")
                failures += 1
                continue
            settings = ref.manual.to_settings(ref.key, ref.platform, ref.league_id)
            source = "manual"
            console.print(f"[yellow]{ref.key}:[/yellow] platform unavailable "
                          f"({str(error).splitlines()[0]}) -- using manual settings")

        save_settings(settings)
        colour = "green" if source == "synced" else "yellow"
        console.print(f"[{colour}]{ref.key}:[/{colour}] {settings.describe()} "
                      f"-- {' '.join(settings.starting_slots)}")
    return 1 if failures else 0


def cmd_rankings(registry: LeagueRegistry, key: str, csv_path: Path | None) -> int:
    """Pull player values for a league and cache them."""
    ref = registry.get(key)
    settings = load_settings(key)
    source = (
        CsvRankingSource(csv_path)
        if csv_path
        else FantasyProsSource(season=int(os.environ.get("SEASON", "2026")))
    )
    try:
        rankings = source.fetch(settings)
    except RuntimeError as error:
        console.print(f"[red]{ref.key}:[/red] {error}")
        return 1
    save_rankings(ref.key, rankings)
    console.print(f"[green]{ref.key}:[/green] cached {len(rankings)} rankings "
                  f"for a {settings.describe()} league")
    return 0


def enriched_board(adapter, key: str, include_unranked: bool = False) -> list[Player]:
    """Available players with ADP and projections attached, if we have any.

    Platforms carry every player who has ever existed; a consensus ranking
    carries the few hundred who will actually be drafted. Anyone outside the
    rankings is dropped by default -- keeping them adds hundreds of zero-value
    entries that crowd the board and could be picked once the reach filter
    excludes everyone real.
    """
    players = adapter.available_players()
    rankings = load_rankings(key)
    if not rankings:
        console.print(f"[yellow]No rankings cached for {key}.[/yellow] "
                      f"Run: draftbot rankings --league {key}")
        return players

    players, unranked = apply_rankings(players, rankings)
    if include_unranked:
        return players

    ranked = [p for p in players if p.adp is not None or p.projected_points is not None]
    console.print(f"[dim]{len(ranked)} ranked players on the board "
                  f"({len(unranked)} unranked hidden; --include-unranked to keep)[/dim]")
    return ranked


def board_from_rankings(key: str) -> list[Player]:
    """A player pool built from the rankings alone, with no platform involved.

    Good enough for a queue, which is just names in preference order. Not good
    enough for a live board, which has to know who has already been taken.
    """
    rankings = load_rankings(key)
    if not rankings:
        console.print(f"[red]No rankings cached for {key}.[/red] "
                      f"Run: draftbot rankings --league {key} --csv <export.csv>")
        return []

    console.print(f"[dim]Building the pool from {len(rankings)} cached rankings "
                  "instead of the platform.[/dim]")
    return [
        Player(
            id=f"rank:{index}",
            name=ranking.name,
            position=ranking.position,
            team=ranking.team,
            adp=ranking.adp,
            projected_points=ranking.projected_points,
            bye_week=ranking.bye_week,
        )
        for index, ranking in enumerate(rankings)
        if ranking.position in {"QB", "RB", "WR", "TE", "K", "DST"}
    ]


def cmd_show(registry: LeagueRegistry, key: str) -> int:
    ref = registry.get(key)
    strategy = Strategy.load(ref.strategy)
    settings = load_settings(key)

    console.print(f"[bold]{ref.key}[/bold] -- {settings.describe()} "
                  f"| strategy: {strategy.name}")
    table = Table("Round", "Prefer", "Avoid", "Gated off")
    rounds = max(
        [p.round for p in strategy.round_plan]
        + list(strategy.earliest_round.values())
        + [len(settings.roster_slots)]
    )
    for number in range(1, rounds + 1):
        plan = strategy.plan_for_round(number)
        gated = [pos for pos, first in strategy.earliest_round.items() if number < first]
        table.add_row(
            str(number),
            ", ".join(plan.prefer) if plan and plan.prefer else "-",
            ", ".join(plan.avoid) if plan and plan.avoid else "-",
            ", ".join(sorted(gated)) or "-",
        )
    console.print(table)
    return 0


def cmd_board(
    registry: LeagueRegistry, key: str, limit: int, include_unranked: bool = False
) -> int:
    """What the bot would take right now, live."""
    ref = registry.get(key)
    adapter = build_adapter(ref)
    strategy = Strategy.load(ref.strategy)
    settings = load_settings(key)

    state = adapter.get_state()
    board = enriched_board(adapter, key, include_unranked)
    ranked = rank_board(state, strategy, settings, board)
    if not ranked:
        console.print("[yellow]No eligible players.[/yellow] "
                      "Constraints may be too tight, or the draft is over.")
        return 1

    console.print(f"[bold]{ref.key}[/bold] round {state.round}, pick {state.pick}")
    table = Table("#", "Player", "Pos", "Team", "ADP", "Score")
    for index, (player, points) in enumerate(ranked[:limit], start=1):
        table.add_row(str(index), player.name, player.position, player.team or "-",
                      f"{player.adp:.0f}" if player.adp else "-", f"{points:.1f}")
    console.print(table)
    return 0


def cmd_queue(registry: LeagueRegistry, key: str, limit: int, out: Path | None) -> int:
    """Export a ranked list to load into the platform's own autodraft queue."""
    ref = registry.get(key)
    strategy = Strategy.load(ref.strategy)
    settings = load_settings(key)

    # A queue is a preference list of names, so it does not actually need the
    # platform -- only the roster shape and the rankings. When the platform
    # can't be read (Yahoo pending API approval, ESPN not implemented), fall
    # back to building the pool from the rankings themselves.
    try:
        board = enriched_board(build_adapter(ref), key)
    except (NotImplementedError, RuntimeError) as error:
        console.print(f"[yellow]{ref.key}: platform unavailable[/yellow] "
                      f"({str(error).splitlines()[0]})")
        board = board_from_rankings(key)
        if not board:
            return 1

    ranked = rank_queue(strategy, settings, board)

    destination = out or Path(f"data/queue_{key}.csv")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "player", "position", "team", "adp"])
        for index, (player, _) in enumerate(ranked[:limit], start=1):
            writer.writerow([index, player.name, player.position, player.team or "",
                             player.adp or ""])
    console.print(f"[green]Wrote {min(limit, len(ranked))} players to {destination}[/green]")
    return 0


def main() -> int:
    load_dotenv()  # credentials live in .env, which is gitignored
    parser = argparse.ArgumentParser(prog="draftbot")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("leagues", help="List configured leagues.")

    auth = sub.add_parser("auth", help="Authorize a platform that needs OAuth.")
    auth.add_argument("platform", choices=["yahoo"])

    sync = sub.add_parser("sync", help="Pull roster and scoring rules from platforms.")
    sync.add_argument("--league", default=None)

    rankings = sub.add_parser("rankings", help="Pull player values for a league.")
    rankings.add_argument("--league", required=True)
    rankings.add_argument(
        "--csv", type=Path, default=None,
        help="Read a rankings CSV export instead of calling the FantasyPros API.",
    )

    show = sub.add_parser("show", help="Print a league's strategy, round by round.")
    show.add_argument("--league", required=True)

    board = sub.add_parser("board", help="Live ranked board for a league.")
    board.add_argument("--league", required=True)
    board.add_argument("--limit", type=int, default=15)
    board.add_argument("--include-unranked", action="store_true",
                       help="Keep players with no ranking (normally hidden).")

    queue = sub.add_parser("queue", help="Export a ranking for the platform's autodraft.")
    queue.add_argument("--league", required=True)
    queue.add_argument("--limit", type=int, default=200)
    queue.add_argument("--out", type=Path, default=None)

    args = parser.parse_args()

    if args.command == "auth" and args.platform == "yahoo":
        try:
            return cmd_auth_yahoo()
        except RuntimeError as error:
            console.print(f"[red]{error}[/red]")
            return 1

    if not args.registry.exists():
        console.print(f"[red]No league registry at {args.registry}.[/red] "
                      "Copy leagues.example.yaml to leagues.yaml.")
        return 1
    registry = LeagueRegistry.load(args.registry)

    if args.command == "leagues":
        return cmd_leagues(registry)
    if args.command == "sync":
        return cmd_sync(registry, args.league)
    if args.command == "rankings":
        return cmd_rankings(registry, args.league, args.csv)
    if args.command == "show":
        return cmd_show(registry, args.league)
    if args.command == "board":
        return cmd_board(registry, args.league, args.limit, args.include_unranked)
    if args.command == "queue":
        return cmd_queue(registry, args.league, args.limit, args.out)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
