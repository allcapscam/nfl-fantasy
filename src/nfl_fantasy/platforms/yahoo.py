"""Yahoo adapter.

Two things make Yahoo harder than Sleeper.

The league key is not the number in the URL. Yahoo keys a league as
`{game_key}.l.{league_id}`, where the game key changes every season. Rather than
hardcode a 2026 game key that will rot, this discovers the user's leagues via
`users;use_login=1/games;game_keys=nfl/leagues` -- `nfl` is an alias for the
current season -- and matches on the league id from `leagues.yaml`.

The JSON is awkward. Yahoo returns collections as objects keyed by numeric
strings alongside a `count`, and represents a single entity as a list of
fragments that have to be merged. The helpers at the top of this module absorb
that so the adapter body stays readable.

Status: written against Yahoo's documented shapes and covered by fixture tests,
but NOT yet run against the live API -- that needs app credentials.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from nfl_fantasy.platforms.base import DraftState, Player
from nfl_fantasy.platforms.yahoo_auth import current_token
from nfl_fantasy.settings import LeagueSettings, Scoring

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"

#: Yahoo's roster slot tokens -> ours.
SLOT_MAP = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "K": "K",
    "DEF": "DST",
    "W/R": "WR_RB_FLEX",
    "W/T": "REC_FLEX",
    "W/R/T": "FLEX",
    "Q/W/R/T": "SUPER_FLEX",
    "BN": "BN",
    "IR": "IR",
}

POSITION_MAP = {"DEF": "DST"}

#: Scoring is read by stat *name*, not id. Yahoo's numeric stat ids are stable
#: in practice but undocumented, and a silently wrong id would mis-score a whole
#: league; names come from the game's own stat_categories resource.
RECEPTION_STAT = "Receptions"
PASS_TD_STAT = "Passing Touchdowns"


# -- Yahoo JSON helpers -------------------------------------------------------


def collection_items(node: Any) -> Iterator[Any]:
    """Yield entries from a Yahoo collection.

    Yahoo writes collections as {"0": {...}, "1": {...}, "count": 2} rather than
    as a list, so ordinary iteration would also yield the count.
    """
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key.isdigit():
            yield value


def merge_fragments(node: Any) -> dict:
    """Flatten Yahoo's list-of-fragments representation of one entity.

    A player arrives as [[{player_key: ...}, {name: {...}}, ...], {...}]; this
    collapses the nesting into a single dict.
    """
    merged: dict = {}

    def absorb(item: Any) -> None:
        if isinstance(item, dict):
            for key, value in item.items():
                if key not in merged or not merged[key]:
                    merged[key] = value
        elif isinstance(item, list):
            for entry in item:
                absorb(entry)

    absorb(node)
    return merged


class YahooAdapter:
    """Reads a Yahoo league and its draft."""

    def __init__(
        self,
        key: str,
        league_id: str,
        client: httpx.Client | None = None,
        league_key: str | None = None,
    ) -> None:
        self.key = key
        self.league_id = str(league_id)
        self._league_key = league_key
        self._client = client or httpx.Client(timeout=30.0)
        self._stat_names: dict[str, str] | None = None

    # -- raw calls -----------------------------------------------------------

    def _get(self, path: str) -> dict:
        token = current_token(self._client)
        response = self._client.get(
            f"{BASE_URL}/{path}",
            headers={"Authorization": f"Bearer {token.access_token}"},
            params={"format": "json"},
        )
        if response.status_code == 401:
            raise RuntimeError(
                "Yahoo rejected the access token. Re-run: draftbot auth yahoo"
            )
        response.raise_for_status()
        return response.json().get("fantasy_content", {})

    def resolve_league_key(self) -> str:
        """Find this league's full key by asking which leagues the user is in."""
        if self._league_key:
            return self._league_key

        content = self._get("users;use_login=1/games;game_keys=nfl/leagues")
        found: list[str] = []
        for user in collection_items(content.get("users", {})):
            merged_user = merge_fragments(user.get("user", []))
            for game in collection_items(merged_user.get("games", {})):
                merged_game = merge_fragments(game.get("game", []))
                for league in collection_items(merged_game.get("leagues", {})):
                    merged = merge_fragments(league.get("league", []))
                    key = merged.get("league_key")
                    if key:
                        found.append(key)
                        if str(merged.get("league_id")) == self.league_id:
                            self._league_key = key
                            return key

        raise RuntimeError(
            f"League {self.league_id} not found on the authorized Yahoo account. "
            f"Leagues visible: {', '.join(found) or 'none'}. "
            "Check the id in leagues.yaml, and that you authorized the right account."
        )

    def stat_names(self) -> dict[str, str]:
        """stat_id -> human name, so scoring can be read without magic numbers."""
        if self._stat_names is not None:
            return self._stat_names
        content = self._get("game/nfl/stat_categories")
        merged = merge_fragments(content.get("game", []))
        stats = (merged.get("stat_categories") or {}).get("stats") or []
        names: dict[str, str] = {}
        for entry in stats:
            stat = entry.get("stat") if isinstance(entry, dict) else None
            if stat and stat.get("stat_id") is not None:
                names[str(stat["stat_id"])] = str(stat.get("name", ""))
        self._stat_names = names
        return names

    # -- protocol ------------------------------------------------------------

    def fetch_settings(self) -> LeagueSettings:
        league_key = self.resolve_league_key()
        content = self._get(f"league/{league_key}/settings")
        merged = merge_fragments(content.get("league", []))
        settings = merge_fragments(merged.get("settings", []))

        slots: list[str] = []
        for entry in settings.get("roster_positions") or []:
            position = entry.get("roster_position") if isinstance(entry, dict) else None
            if not position:
                continue
            token = str(position.get("position", ""))
            count = int(position.get("count", 0) or 0)
            slots.extend([SLOT_MAP.get(token, token)] * count)

        return LeagueSettings(
            key=self.key,
            platform="yahoo",
            league_id=self.league_id,
            name=str(merged.get("name", "")),
            teams=int(merged.get("num_teams") or 12),
            draft_type="auction" if settings.get("is_auction_draft") in (1, "1") else "snake",
            roster_slots=slots,
            scoring=self.parse_scoring(settings, self.stat_names()),
        )

    @staticmethod
    def parse_scoring(settings: dict, stat_names: dict[str, str]) -> Scoring:
        """Read the few scoring rules that change draft strategy."""
        modifiers = (settings.get("stat_modifiers") or {}).get("stats") or []
        values: dict[str, float] = {}
        for entry in modifiers:
            stat = entry.get("stat") if isinstance(entry, dict) else None
            if not stat:
                continue
            name = stat_names.get(str(stat.get("stat_id")), "")
            try:
                values[name] = float(stat.get("value", 0) or 0)
            except (TypeError, ValueError):
                continue

        return Scoring(
            reception=values.get(RECEPTION_STAT, 0.0),
            pass_td=values.get(PASS_TD_STAT, 4.0),
        )

    def draft_results(self) -> list[dict]:
        league_key = self.resolve_league_key()
        content = self._get(f"league/{league_key}/draftresults")
        merged = merge_fragments(content.get("league", []))
        results = []
        for entry in collection_items(merged.get("draft_results", {})):
            result = merge_fragments(entry.get("draft_result", {}))
            if result.get("player_key"):
                results.append(result)
        return results

    def get_state(self) -> DraftState:
        settings = self.fetch_settings()
        picks = self.draft_results()
        made = len(picks)
        teams = max(1, settings.teams)
        return DraftState(
            round=made // teams + 1,
            pick=made % teams + 1,
            on_the_clock=False,
            my_roster=[],
            drafted_player_ids={str(p["player_key"]) for p in picks},
        )

    @staticmethod
    def parse_player(node: Any) -> Player | None:
        merged = merge_fragments(node)
        key = merged.get("player_key")
        if not key:
            return None
        name = merged.get("name") or {}
        position = str(merged.get("display_position") or "")
        # Multi-position players come through as "RB,WR"; take the first.
        position = position.split(",")[0].strip().upper()
        return Player(
            id=str(key),
            name=str(name.get("full") or "").strip(),
            position=POSITION_MAP.get(position, position),
            team=(str(merged.get("editorial_team_abbr") or "").upper() or None),
            bye_week=None,
        )

    def available_players(self, limit: int = 600) -> list[Player]:
        """Undrafted players. Yahoo pages 25 at a time, so this walks pages."""
        league_key = self.resolve_league_key()
        players: list[Player] = []
        start = 0
        page_size = 25

        while start < limit:
            content = self._get(
                f"league/{league_key}/players;status=A;start={start};count={page_size}"
            )
            merged = merge_fragments(content.get("league", []))
            page = list(collection_items(merged.get("players", {})))
            if not page:
                break
            for entry in page:
                player = self.parse_player(entry.get("player", []))
                if player and player.name:
                    players.append(player)
            if len(page) < page_size:
                break
            start += page_size

        return players
