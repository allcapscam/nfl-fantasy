"""Command line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.table import Table

from nfl_fantasy.strategy import Strategy

console = Console()


def show_strategy(path: Path) -> int:
    """Load a strategy file and print what it will do, round by round."""
    strategy = Strategy.load(path)
    console.print(f"[bold]{strategy.name}[/bold] "
                  f"({strategy.league.teams}-team {strategy.league.scoring}, "
                  f"slot {strategy.league.draft_slot})")

    table = Table("Round", "Prefer", "Avoid", "Gated off")
    rounds = max(
        [p.round for p in strategy.round_plan] + list(strategy.earliest_round.values()) + [1]
    )
    for round_number in range(1, rounds + 1):
        plan = strategy.plan_for_round(round_number)
        gated = [
            pos for pos, first in strategy.earliest_round.items() if round_number < first
        ]
        table.add_row(
            str(round_number),
            ", ".join(plan.prefer) if plan else "-",
            ", ".join(plan.avoid) if plan and plan.avoid else "-",
            ", ".join(sorted(gated)) or "-",
        )
    console.print(table)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="draftbot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="Validate a strategy file and print its plan.")
    show.add_argument("--strategy", type=Path, default=Path("strategy.yaml"))

    sub.add_parser("draft", help="Run the bot against a live draft.")

    args = parser.parse_args()

    if args.command == "show":
        path = args.strategy
        if not path.exists():
            console.print(f"[red]No strategy file at {path}.[/red] "
                          "Copy strategy.example.yaml to strategy.yaml to start.")
            return 1
        return show_strategy(path)

    if args.command == "draft":
        console.print("[yellow]No platform adapter is implemented yet.[/yellow] "
                      "Add one under src/nfl_fantasy/platforms/ implementing DraftPlatform.")
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
