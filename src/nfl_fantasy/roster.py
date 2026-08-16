"""Roster construction: what your lineup still needs.

This is where differing roster rules across leagues actually bite. A superflex
league wants a second QB far earlier than a single-QB league; a 3-WR league
values the WR3 that a 2-WR league leaves on the bench. Rather than encode that
per league by hand, work it out from the slot list the platform reported.
"""

from __future__ import annotations

from collections import Counter

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.settings import FLEX_SLOTS, LeagueSettings, slot_accepts


def fill_lineup(roster: list[Player], settings: LeagueSettings) -> dict[str, Player | None]:
    """Greedily place rostered players into starting slots.

    Dedicated slots are filled before flex slots, so a flex isn't wasted on a
    player who has a dedicated home.
    """
    slots = settings.starting_slots
    assignment: dict[str, Player | None] = {}
    remaining = list(roster)

    ordered = [s for s in slots if s not in FLEX_SLOTS] + [s for s in slots if s in FLEX_SLOTS]
    for index, slot in enumerate(ordered):
        match = next((p for p in remaining if slot_accepts(slot, p.position)), None)
        if match:
            remaining.remove(match)
        assignment[f"{slot}#{index}"] = match
    return assignment


def unfilled_slots(roster: list[Player], settings: LeagueSettings) -> list[str]:
    """Starting slots with nobody in them yet."""
    return [
        key.split("#")[0]
        for key, player in fill_lineup(roster, settings).items()
        if player is None
    ]


def needs_by_position(roster: list[Player], settings: LeagueSettings) -> dict[str, int]:
    """How many more of each position the starting lineup can still absorb."""
    have = Counter(p.position for p in roster)
    return {
        position: max(0, settings.max_startable(position) - have[position])
        for position in ("QB", "RB", "WR", "TE", "K", "DST")
    }


def need_multiplier(position: str, roster: list[Player], settings: LeagueSettings) -> float:
    """Bonus for a position that fills an empty starting slot.

    Deliberately gentle -- need should break ties between comparable players,
    not drag a clearly worse player up the board.
    """
    open_slots = unfilled_slots(roster, settings)
    if any(slot_accepts(slot, position) for slot in open_slots):
        return 1.10
    if needs_by_position(roster, settings)[position] > 0:
        return 1.0
    return 0.85  # already have every startable spot covered
