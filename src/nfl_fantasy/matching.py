"""Matching ranking-source players to platform players.

Sources and platforms disagree about names constantly -- suffixes, punctuation,
and defenses especially ("Kenneth Walker III" vs "Kenneth Walker", "D.K.
Metcalf" vs "DK Metcalf", "Chiefs D/ST" vs "Kansas City Chiefs"). Get this wrong
and the bot silently drops good players off the board, so it is kept in one
place and tested.
"""

from __future__ import annotations

import re
import unicodedata

from nfl_fantasy.platforms.base import Player
from nfl_fantasy.sources.base import Ranking

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

#: Sites disagree about team codes. Everything maps to Sleeper's spelling.
#: FantasyPros writes Jacksonville as JAC where Sleeper writes JAX, which
#: silently broke the Jaguars defense until it was caught by diffing the two
#: code sets against each other.
TEAM_ALIASES = {
    "JAC": "JAX",
    "OAK": "LV",
    "SD": "LAC",
    "SL": "LAR",
    "STL": "LAR",
    "LA": "LAR",
    "WSH": "WAS",
    "WFT": "WAS",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
}


def normalize_team(team: str | None) -> str | None:
    """Map a team code to Sleeper's spelling."""
    if not team:
        return None
    code = team.strip().upper()
    return TEAM_ALIASES.get(code, code)


def normalize_name(name: str) -> str:
    """Reduce a name to a comparable key."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"\b(d/st|dst|defense)\b", "", text)
    text = re.sub(r"[^a-z\s]", "", text)  # drops periods, apostrophes, hyphens
    parts = [p for p in text.split() if p and p not in SUFFIXES]
    return " ".join(parts)


def match_key(name: str, position: str, team: str | None) -> str:
    """Defenses match on team code; everyone else on name.

    Names are useless for defenses -- a source says "Chiefs D/ST" where the
    platform says "Kansas City Chiefs" -- so the team code is the identity. Fall
    back to the last word of the name only when no team code is given.
    """
    if position == "DST":
        code = normalize_team(team) or ""
        if not code:
            words = normalize_name(name).split()
            code = words[-1].upper() if words else ""
        return f"dst:{code}"
    return f"{position.lower()}:{normalize_name(name)}"


def index_rankings(rankings: list[Ranking]) -> dict[str, Ranking]:
    return {match_key(r.name, r.position, r.team): r for r in rankings}


def apply_rankings(
    players: list[Player], rankings: list[Ranking]
) -> tuple[list[Player], list[Player]]:
    """Attach ADP and projections to platform players.

    Returns (enriched, unmatched). Unmatched players are handed back rather than
    hidden -- a long unmatched list means the name matching needs work, and that
    should be visible instead of quietly shrinking the board.
    """
    index = index_rankings(rankings)
    enriched: list[Player] = []
    unmatched: list[Player] = []

    for player in players:
        ranking = index.get(match_key(player.name, player.position, player.team))
        if ranking is None:
            unmatched.append(player)
            enriched.append(player)
            continue
        enriched.append(
            player.model_copy(
                update={
                    "adp": ranking.adp if ranking.adp is not None else player.adp,
                    "projected_points": (
                        ranking.projected_points
                        if ranking.projected_points is not None
                        else player.projected_points
                    ),
                    "bye_week": ranking.bye_week or player.bye_week,
                }
            )
        )
    return enriched, unmatched
