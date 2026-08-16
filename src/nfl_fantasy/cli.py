"""Command line entry point."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from rich.console import Console
from rich.table import Table

from nfl_fantasy.draft import rank_board
from nfl_fantasy.leagues import LeagueRef, LeagueRegistry
from nfl_fantasy.platforms.base import DraftState
from nfl_fantasy.platforms.sleeper import SleeperAdapter
from nfl_fantasy.store import load_settings, save_settings
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
    raise NotImplementedError(
        f"No adapter for {ref.platform!r} yet. Sleeper is implemented; "
        "ESPN and Yahoo are next."
    )


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
        try:
            settings = build_adapter(ref).fetch_settings()
        except Exception as error:  # noqa: BLE001 - report and continue to the next league
            console.print(f"[red]{ref.key}:[/red] {error}")
            failures += 1
            continue
        save_settings(settings)
        console.print(f"[green]{ref.key}:[/green] {settings.describe()} "
                      f"-- {' '.join(settings.starting_slots)}")
    return 1 if failures else 0


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


def cmd_board(registry: LeagueRegistry, key: str, limit: int) -> int:
    """What the bot would take right now, live."""
    ref = registry.get(key)
    adapter = build_adapter(ref)
    strategy = Strategy.load(ref.strategy)
    settings = load_settings(key)

    state = adapter.get_state()
    ranked = rank_board(state, strategy, settings, adapter.available_players())
    if not ranked:
        console.print("[yellow]No eligible players.[/yellow] "
                      "Constraints may be too tight, or the draft is over.")
        return 1

    console.print(f"[bold]{ref.key}[/bold] round {state.round}, pick {state.pick}")
    table = Table("#", "Player", "Pos", "Team", "Score")
    for index, (player, points) in enumerate(ranked[:limit], start=1):
        table.add_row(str(index), player.name, player.position,
                      player.team or "-", f"{points:.1f}")
    console.print(table)
    return 0


def cmd_queue(registry: LeagueRegistry, key: str, limit: int, out: Path | None) -> int:
    """Export a ranked list to load into the platform's own autodraft queue."""
    ref = registry.get(key)
    adapter = build_adapter(ref)
    strategy = Strategy.load(ref.strategy)
    settings = load_settings(key)

    state = DraftState(round=1, pick=settings.draft_slot or 1)
    ranked = rank_board(state, strategy, settings, adapter.available_players())

    destination = out or Path(f"data/queue_{key}.csv")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "player", "position", "team"])
        for index, (player, _) in enumerate(ranked[:limit], start=1):
            writer.writerow([index, player.name, player.position, player.team or ""])
    console.print(f"[green]Wrote {min(limit, len(ranked))} players to {destination}[/green]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="draftbot")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("leagues", help="List configured leagues.")

    sync = sub.add_parser("sync", help="Pull roster and scoring rules from platforms.")
    sync.add_argument("--league", default=None)

    show = sub.add_parser("show", help="Print a league's strategy, round by round.")
    show.add_argument("--league", required=True)

    board = sub.add_parser("board", help="Live ranked board for a league.")
    board.add_argument("--league", required=True)
    board.add_argument("--limit", type=int, default=15)

    queue = sub.add_parser("queue", help="Export a ranking for the platform's autodraft.")
    queue.add_argument("--league", required=True)
    queue.add_argument("--limit", type=int, default=200)
    queue.add_argument("--out", type=Path, default=None)

    args = parser.parse_args()

    if not args.registry.exists():
        console.print(f"[red]No league registry at {args.registry}.[/red] "
                      "Copy leagues.example.yaml to leagues.yaml.")
        return 1
    registry = LeagueRegistry.load(args.registry)

    if args.command == "leagues":
        return cmd_leagues(registry)
    if args.command == "sync":
        return cmd_sync(registry, args.league)
    if args.command == "show":
        return cmd_show(registry, args.league)
    if args.command == "board":
        return cmd_board(registry, args.league, args.limit)
    if args.command == "queue":
        return cmd_queue(registry, args.league, args.limit, args.out)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
