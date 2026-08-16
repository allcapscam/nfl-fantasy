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

Pull player values (needs `FANTASYPROS_API_KEY`):

```bash
uv run draftbot rankings --league home
```

No API key? Export rankings from FantasyPros as CSV and read that instead:

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
FantasyPros rankings via API or CSV export, name matching (verified against the
live Sleeper player list), the Sleeper adapter, queue export, 36 tests.

**Not built yet:**

- **ESPN and Yahoo adapters.** The protocol is defined and Sleeper implements it.
  ESPN needs `espn_s2`/`SWID` cookies for private leagues; Yahoo needs a
  registered OAuth app.
- **Real projections.** The FantasyPros consensus endpoint gives expert rank,
  which stands in for ADP. Actual point projections are a separate endpoint and
  aren't wired up, so `value_of` still ranks by ADP rather than projected points.
- **Strategy/format conflict warnings.** Nothing yet catches a strategy that
  gates QB until round 6 being pointed at a superflex league, where that's a bad
  idea.
- **Auction and keeper/dynasty formats.** The engine assumes a snake draft.

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
