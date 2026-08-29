# Handoff — NFL Fantasy Draft Bot

Pick-up notes for continuing this project on another machine. Written 2026-08-20.

**Repo:** https://github.com/allcapscam/nfl-fantasy (public)
**State:** clean and pushed. 60 tests passing, ruff clean.

---

## What this is

A bot that drafts fantasy football rosters from a strategy you write, across
four leagues on three platforms (2× Yahoo, 1× Sleeper, 1× ESPN) with different
roster rules.

**It does not click your picks.** No platform allows API draft submission —
Sleeper's API is explicitly read-only, Yahoo's is read-access only, ESPN has no
public API. Browser automation would violate all three ToS. Instead it does the
two things that are legitimately automatable:

- `queue` — exports a ranked list you load into the platform's own autodraft
- `board` — reads the live draft and tells you who to take right now

This was verified, not assumed. Don't relitigate it without new evidence.

---

## Getting running on a new machine

```bash
git clone https://github.com/allcapscam/nfl-fantasy
cd nfl-fantasy
uv sync
```

Requires [uv](https://docs.astral.sh/uv/). System Python is not used; uv pulls
its own toolchain (currently CPython 3.14).

### Four things git does NOT carry

They are gitignored on purpose — the repo is public. Copy them across, or
recreate from the `.example` templates:

| File | What it holds | Recreate from |
| --- | --- | --- |
| `.env` | FantasyPros API key, Sleeper user id | `.env.example` |
| `leagues.yaml` | The four league ids | `leagues.example.yaml` |
| `strategies/*.yaml` | Draft strategies | `strategies/*.example.yaml` |
| `data/` | Synced settings, cached rankings, queues | regenerate, see below |

The league ids and setup specifics are in the Claude memory store for this
project, which syncs across machines. `data/` is fully regenerable:

```bash
uv run draftbot sync
uv run draftbot rankings --league sleeper --csv <path-to-FantasyPros-halfppr.csv>
uv run draftbot queue --league sleeper --limit 192
```

### Verify the environment is good

```bash
uv run pytest -q && uv run ruff check .
```

46 passing, no lint errors. If that holds, the checkout is sound.

---

## Architecture

Three concerns kept separate so a strategy is portable across leagues:

| Piece | File | Job |
| --- | --- | --- |
| League registry | `leagues.yaml` | Which leagues, which platform, which strategy |
| Synced settings | `data/settings/*.json` | Roster slots + scoring, pulled from platform |
| Strategy | `strategies/*.yaml` | Your rules. No league specifics |
| Roster logic | `src/nfl_fantasy/roster.py` | What the lineup still needs |
| Engine | `src/nfl_fantasy/draft.py` | Scores and ranks the board |
| Matching | `src/nfl_fantasy/matching.py` | Ranking names -> platform players |
| Adapters | `src/nfl_fantasy/platforms/` | Per-platform reads |
| Sources | `src/nfl_fantasy/sources/` | Where player value comes from |

Roster rules are **fetched from each platform**, never hand-typed, so they can't
drift. That is why the same strategy file produces different picks per league.

---

## Hard-won findings — do not rediscover these

Each cost real debugging. All are guarded in code with regression tests.

1. **FantasyPros free-tier keys return 10 players.** Response carries
   `public_api_limited: true` and `tier: "free"`, and ignores `limit`, `offset`,
   `page`, `per_page`. The current key is free tier. `fetch()` raises
   `FreeTierError` rather than draft off a 10-player board. **The CSV export is
   the working path.**

2. **Position-filtered ranks are not comparable.** Requesting `position=RB`
   renumbers `rank_ecr` from 1, so the K1 and RB1 both come back as rank 1.
   Merging per-position calls would rank a kicker #1 overall. Only
   `position=ALL` (or `OP` for superflex) gives true overall ranks. Projections
   are exempt — points are absolute, so those merge safely.

3. **`JAC` vs `JAX`.** FantasyPros spells Jacksonville `JAC`, Sleeper `JAX`.
   Silently broke the Jaguars defense. See `TEAM_ALIASES` in `matching.py`.

4. **Value must decay non-linearly.** `1000 - rank` made the top 11 span ~1%
   while a prefer bonus was 15%, so soft preferences outranked value by ~100
   places. Now halves every 30 ranks (`RANK_HALF_LIFE`).

5. **Yahoo's league key is not the number in the URL.** It is
   `{game_key}.l.{league_id}` and the game key changes every season. The adapter
   discovers it via `users;use_login=1/games;game_keys=nfl/leagues` (`nfl` is an
   alias for the current season) and matches on the id in `leagues.yaml`. Don't
   hardcode a game key.

6. **Yahoo scoring is read by stat name, not stat id.** The numeric ids are
   undocumented, and a wrong one would silently mis-score a whole league. The
   adapter pulls `game/nfl/stat_categories` and matches on "Receptions".

7. **The queue is not the board.** It skips the reach limit (a per-pick idea),
   demotes gated players rather than dropping them (else you lose the TE1
   forever), and promotes required starters into the draft — consensus ranks put
   the first kicker past the last pick, which would end the draft with an empty
   K slot.

---

## Where things stand

### Working and verified against the real league

Sleeper league **CFWP**: 12-team, half-PPR (0.5/rec, no
TE premium), snake, 16 rounds, 90s picks, roster `QB RB RB WR WR TE FLEX K DST`
+ 7 bench.

- 878 rankings loaded from the half-PPR CSV export
- **100% match** across the top 250 draftable players, all 32 defenses
- Queue exports 192 roster-valid picks; gates land exactly right (TE round 3,
  QB round 4, K slot 157, DST slot 187)

### Not built

- **ESPN adapter** — needs `espn_s2` + `SWID` cookies from
  a logged-in browser session. The last platform left.
- **Strategy/format conflict warnings** — nothing catches a strategy gating QB
  until round 6 pointed at a superflex league.
- **Auction and keeper/dynasty formats** — the engine assumes a snake draft.

### Open items for the user

- **Sleeper username** still needed. Without `SLEEPER_USER_ID` the bot can't tell
  which picks are yours, disabling need-based scoring in `board`. The queue
  doesn't need it.
- **Draft order isn't drawn yet**, so the draft slot is unknown. Re-run `sync`
  once it is.
- **`strategies/balanced.yaml` is still the untouched example.** The whole
  premise is that the strategy is the user's own. Worth tuning before draft day,
  especially `reach_tolerance: 10`, which currently limits each pick to an
  11-player window.
- **Re-export rankings shortly before drafting** — consensus ranks move a lot in
  late August, and the CSV is a point-in-time snapshot.

---

## Suggested next step

Build the ESPN adapter. It implements the same `DraftPlatform` protocol in
`platforms/base.py` that `platforms/sleeper.py` already satisfies, so the engine,
matching, and queue all work unchanged once it can return `LeagueSettings`,
`DraftState`, and a player list. Watch for ESPN's own team-code and slot-id
spellings — expect to extend `TEAM_ALIASES` and the slot map.
