"""Where the projection and the market disagree, and what to do about it.

Projections go stale. During the ESPN draft the model kept offering Josh Jacobs
as the best value on the board -- 259 points, going 30 picks later than that
deserves -- while he was in fact on the commissioner's exempt list, unable to
practise or play. The feed had not caught up. The market had.

That is the general shape of the failure: a projection is a snapshot, and a
large gap between it and ADP is far more often stale data than free value. This
module makes the model say so out loud rather than presenting the gap as an
opportunity, and gives a place to record news the feed does not carry.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

#: How far a player's value rank must sit ahead of his draft position before
#: the gap stops looking like an edge and starts looking like missing news.
SUSPICIOUS_GAP = 25


@dataclass(frozen=True)
class Flag:
    """A manual note about a player the projections cannot know."""

    action: str  # "exclude" or "downgrade"
    reason: str

    @property
    def excluded(self) -> bool:
        return self.action == "exclude"


def load_flags(path: str | Path) -> dict[str, Flag]:
    """Hand-entered notes: suspensions, exempt lists, late injuries.

    Keyed by normalized name. The file is written by whoever spots the news;
    nothing infers these.
    """
    from nfl_fantasy.advisor import normalize

    flags: dict[str, Flag] = {}
    file = Path(path)
    if not file.exists():
        return flags
    with file.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("name") or "").strip()
            action = (row.get("action") or "downgrade").strip().lower()
            if name:
                flags[normalize(name)] = Flag(action, (row.get("reason") or "").strip())
    return flags


def load_byes(path: str | Path) -> dict[str, int]:
    """Bye week by normalized name, from a side-car file.

    Kept separate from the projections because platforms differ on whether they
    ship byes at all -- Yahoo's player list carries them, ESPN's projection
    payload does not. A missing file simply means no bye awareness.
    """
    from nfl_fantasy.advisor import normalize

    byes: dict[str, int] = {}
    file = Path(path)
    if not file.exists():
        return byes
    with file.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("name") or "").strip()
            try:
                week = int(float(row.get("bye") or 0))
            except (TypeError, ValueError):
                continue
            if name and week:
                byes[normalize(name)] = week
    return byes


def market_disagreement(value_rank: int, adp: float | None) -> int | None:
    """How many picks later than his value the market is taking a player.

    A positive result means the model likes him more than the room does. Past
    `SUSPICIOUS_GAP` that is worth a second look rather than a celebration.
    """
    if adp is None:
        return None
    gap = int(adp - value_rank)
    return gap if gap >= SUSPICIOUS_GAP else None


def disagreement_note(gap: int | None) -> str | None:
    """Wording for a gap big enough to be suspicious."""
    if gap is None:
        return None
    return f"market drafts him {gap} picks later than his value -- check for news"
