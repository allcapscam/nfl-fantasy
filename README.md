# NFL Fantasy Draft Bot

Drafts your fantasy football rosters according to a strategy you write — across
several leagues on Sleeper, ESPN, and Yahoo at once.

Your edge is the strategy, not the clicking. You describe how you want to draft
— which positions to gate off until later rounds, what to prioritize each round,
how far you'll reach off ADP — and the bot applies it consistently in every
league, adapting it to each league's actual roster rules.

## Read this first: the platforms are read-only

None of the three platforms let a third-party tool submit a draft pick.

- **Sleeper** — the API is explicitly read-only: "No API Token is necessary, as
  you cannot modify contents via this API." There is no make-pick endpoint.
- **Yahoo** — the Fantasy Sports API grants read access only.
- **ESPN** — has no public API at all; the endpoints in common use are
  undocumented and read-only in practice.

So this tool does not click your picks. It does the two things that are
legitimately automatable, which get you most of the way there:

1. **`queue`** — exports a ranked player list, per league, built from your
   strategy. You load it into that platform's own draft queue / custom rankings,
   and the platform's native autodraft executes your strategy when you're away.
2. **`board`** — reads the live draft and tells you who to take right now, given
   your strategy, your roster so far, and who's already gone. For when you're at
   the keyboard.

Browser automation could click for you, but it's fragile and violates all three
platforms' terms of service, so it isn't built here.

## Multiple leagues

Leagues differ in ways that change the right pick, so the bot pulls each
league's real rules from its platform rather than trusting you to retype them:

```bash
uv run draftbot sync
```

That writes roster slots and scoring per league. From then on the same strategy
file behaves differently where the league differs — a superflex league takes the
QB over the equally-ranked RB; a TE-premium league lifts tight ends; a 3-WR
league wants the WR3 a 2-WR league benches. There is a test for exactly this in
[test_draft.py](tests/test_draft.py) — same strategy, same board, opposite pick.

`leagues.yaml` is the registry:

```yaml
leagues:
  home:
    platform: sleeper
    league_id: "123456789"
    strategy: strategies/balanced.yaml
  work:
    platform: espn
    league_id: "456789"
    strategy: strategies/balanced.yaml
```

## How it works

| Piece | File | Job |
| --- | --- | --- |
| League registry | `leagues.yaml` | Which leagues, which platform, which strategy |
| Synced settings | `data/settings/*.json` | Roster slots + scoring, pulled from the platform |
| Strategy | `strategies/*.yaml` | Your rules. Portable across leagues |
| Roster logic | [roster.py](src/nfl_fantasy/roster.py) | What your lineup still needs |
| Engine | [draft.py](src/nfl_fantasy/draft.py) | Scores the board, ranks it |
| Adapters | [platforms/](src/nfl_fantasy/platforms/) | Per-platform reads |

Strategy rules come in two kinds:

- **Hard constraints** (`earliest_round`, `max_per_position`) — never violated.
  This is how you say "no kicker before round 14."
- **Soft preferences** (`round_plan`, `position_weight`) — these rank the players
  that already passed the constraints. This is how you say "I'd like a WR in
  round 1, but take the value if it falls."

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
cp leagues.example.yaml leagues.yaml
cp strategies/balanced.example.yaml strategies/balanced.yaml
cp .env.example .env
```

`leagues.yaml`, `strategies/*.yaml`, and `.env` are gitignored — this repo is
public, so your real strategy and league credentials stay on your machine.

## Usage

```bash
uv run draftbot leagues
```

```bash
uv run draftbot sync
```

Pull player values (needs `FANTASYPROS_API_KEY` in `.env`):

```bash
uv run draftbot rankings --league home
```

Free-tier keys return only 10 players — see below. The CSV export has no such
limit:

```bash
uv run draftbot rankings --league home --csv ~/Downloads/FantasyPros_Rankings.csv
```

```bash
uv run draftbot show --league home
```

```bash
uv run draftbot board --league home
```

```bash
uv run draftbot queue --league home
```

## Status

**Working:** multi-league registry, normalized settings model across platforms,
roster/flex logic including superflex and TE-premium, the ranking engine,
FantasyPros rankings and projections via API or CSV export, name matching
(100% on the draftable range against a live Sleeper league), the Sleeper
adapter, roster-valid queue export, 46 tests.

### How the queue differs from the board

`board` answers "who do I take at this pick"; `queue` produces a static list the
platform's autodraft consumes top-down. They are not the same ranking:

- The **reach limit** is skipped in the queue. "Too early for this pick" is
  meaningless in a list spanning every pick.
- **Round gates demote rather than drop.** A TE gated until round 3 who ranks in
  round 2 stays in the queue, moved to round 3 — otherwise you'd never take the
  TE1 even if he fell to you.
- **Required starters are guaranteed.** Consensus rankings put the first kicker
  below the last pick of the draft, because a kicker is nearly worthless per
  rank. But a roster with a K slot must fill it, so positions with dedicated
  starting slots are promoted to their gate round. Without this the autodraft
  ends with holes in the lineup.

**Not built yet:**

- **ESPN and Yahoo adapters.** The protocol is defined and Sleeper implements it.
  ESPN needs `espn_s2`/`SWID` cookies for private leagues; Yahoo needs a
  registered OAuth app.
- **Strategy/format conflict warnings.** Nothing yet catches a strategy that
  gates QB until round 6 being pointed at a superflex league, where that's a bad
  idea.
- **Auction and keeper/dynasty formats.** The engine assumes a snake draft.

### Two FantasyPros traps

Both were found by probing the live API, and both are guarded in code.

**Free-tier keys return 10 players.** The response carries
`public_api_limited: true`, `tier: "free"`, and exactly ten records regardless of
any `limit`, `offset`, `page`, or `per_page` you pass. Ten players cannot fill a
sixteen-round draft, so [fantasypros.py](src/nfl_fantasy/sources/fantasypros.py)
raises `FreeTierError` rather than hand back a board that short. Use the CSV
export, or a production key from a HOF subscription.

**Position-filtered ranks are not comparable.** Requesting `position=RB`
renumbers `rank_ecr` from 1 — so the K1 and the RB1 both come back as rank 1.
Merging per-position calls to rebuild a full board would rank a kicker first
overall. Only `position=ALL` (or `OP` for superflex) returns true overall ranks,
and this module never merges per-position ranking calls. Projections are the
exception and are merged across positions, because points are an absolute scale.

### A note on name matching

Ranking sites and platforms disagree about names constantly — suffixes, periods,
apostrophes, and defenses especially. [matching.py](src/nfl_fantasy/matching.py)
normalizes both sides, and unmatched players are reported rather than silently
dropped, because a quietly shrinking board is the worst possible failure here.
Defenses match on team code, not name.

## Tests

```bash
uv run pytest
```
