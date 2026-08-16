"""Sleeper adapter.

Sleeper's API is public and needs no authentication, because it is read-only:
"No API Token is necessary, as you cannot modify contents via this API."
That makes it the easiest platform to read and the one we can verify live.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from nfl_fantasy.platforms.base import DraftState, Player
from nfl_fantasy.settings import LeagueSettings, Scoring

BASE_URL = "https://api.sleeper.app/v1"
CACHE_DIR = Path("data/cache")
PLAYER_CACHE_TTL = 60 * 60 * 24  # Sleeper asks callers to pull players at most daily.

#: Sleeper's slot tokens -> ours.
SLOT_MAP = {
    "DEF": "DST",
    "WRRB_FLEX": "WR_RB_FLEX",
    "REC_FLEX": "REC_FLEX",
    "SUPER_FLEX": "SUPER_FLEX",
    "FLEX": "FLEX",
}

POSITION_MAP = {"DEF": "DST"}


class SleeperAdapter:
    """Reads a Sleeper league and its draft."""

    def __init__(
        self,
        key: str,
        league_id: str,
        draft_id: str | None = None,
        user_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.key = key
        self.league_id = league_id
        self.draft_id = draft_id
        self.user_id = user_id
        self._client = client or httpx.Client(timeout=20.0)

    # -- raw calls -------------------------------------------------------

    def _get(self, path: str) -> Any:
        response = self._client.get(f"{BASE_URL}{path}")
        response.raise_for_status()
        return response.json()

    def league(self) -> dict[str, Any]:
        return self._get(f"/league/{self.league_id}")

    def resolve_draft_id(self) -> str:
        """Use the configured draft, else the league's most recent one."""
        if self.draft_id:
            return self.draft_id
        drafts = self._get(f"/league/{self.league_id}/drafts")
        if not drafts:
            raise RuntimeError(f"Sleeper league {self.league_id} has no drafts.")
        self.draft_id = drafts[0]["draft_id"]
        return self.draft_id

    def draft(self) -> dict[str, Any]:
        return self._get(f"/draft/{self.resolve_draft_id()}")

    def picks(self) -> list[dict[str, Any]]:
        return self._get(f"/draft/{self.resolve_draft_id()}/picks")

    def all_players(self) -> dict[str, Any]:
        """The full player dictionary, cached to disk -- it is a ~5MB response."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache = CACHE_DIR / "sleeper_players.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < PLAYER_CACHE_TTL:
            return json.loads(cache.read_text(encoding="utf-8"))
        data = self._get("/players/nfl")
        cache.write_text(json.dumps(data), encoding="utf-8")
        return data

    # -- protocol --------------------------------------------------------

    def fetch_settings(self) -> LeagueSettings:
        league = self.league()
        draft = self.draft()

        slots = [SLOT_MAP.get(s, s) for s in league.get("roster_positions", [])]
        scoring_raw = league.get("scoring_settings") or {}
        scoring = Scoring(
            reception=float(scoring_raw.get("rec", 0.0)),
            te_reception_bonus=float(scoring_raw.get("bonus_rec_te", 0.0)),
            pass_td=float(scoring_raw.get("pass_td", 4.0)),
        )

        draft_slot = None
        order = draft.get("draft_order") or {}
        if self.user_id and self.user_id in order:
            draft_slot = int(order[self.user_id])

        return LeagueSettings(
            key=self.key,
            platform="sleeper",
            league_id=self.league_id,
            name=league.get("name", ""),
            teams=int(league.get("total_rosters") or draft.get("settings", {}).get("teams", 12)),
            draft_slot=draft_slot,
            draft_type=draft.get("type", "snake"),
            roster_slots=slots,
            scoring=scoring,
        )

    def _to_player(self, player_id: str, record: dict[str, Any]) -> Player:
        position = record.get("position") or ""
        return Player(
            id=player_id,
            name=record.get("full_name") or f"{record.get('first_name', '')} {record.get('last_name', '')}".strip(),
            position=POSITION_MAP.get(position, position),
            team=record.get("team"),
            bye_week=record.get("bye_week"),
        )

    def get_state(self) -> DraftState:
        draft = self.draft()
        picks = self.picks()
        players = self.all_players()

        teams = int(draft.get("settings", {}).get("teams") or 12)
        made = len(picks)
        round_number = made // teams + 1
        pick_in_round = made % teams + 1

        my_roster: list[Player] = []
        if self.user_id:
            for pick in picks:
                if pick.get("picked_by") == self.user_id:
                    pid = str(pick.get("player_id"))
                    if pid in players:
                        my_roster.append(self._to_player(pid, players[pid]))

        return DraftState(
            round=round_number,
            pick=pick_in_round,
            on_the_clock=draft.get("status") == "drafting",
            my_roster=my_roster,
            drafted_player_ids={str(p.get("player_id")) for p in picks},
        )

    def available_players(self) -> list[Player]:
        drafted = {str(p.get("player_id")) for p in self.picks()}
        players = self.all_players()
        return [
            self._to_player(pid, record)
            for pid, record in players.items()
            if pid not in drafted
            and record.get("position") in {"QB", "RB", "WR", "TE", "K", "DEF"}
            and record.get("team")
        ]
