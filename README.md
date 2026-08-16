# NFL Fantasy Draft Bot

A bot that runs your fantasy football draft for you, following a strategy you write.

The idea: your edge is the strategy, not the clicking. You describe how you want
to draft — which positions to gate off until later rounds, what to prioritize in
each round, how far you're willing to reach off ADP — and the bot executes it
pick after pick without getting talked out of the plan at 11pm on a Thursday.

## How it works

Three pieces, kept separate on purpose:

| Piece | File | Job |
| --- | --- | --- |
| Strategy | `strategy.yaml` | Your rules, in YAML. No code. |
| Engine | `src/nfl_fantasy/draft.py` | Scores the board against the strategy and picks. |
| Platform adapter | `src/nfl_fantasy/platforms/` | Talks to Sleeper / ESPN / Yahoo. |

The engine never knows which platform it's on, so you can test a strategy against
a mock draft and then point it at the real thing unchanged.

Rules come in two kinds:

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

Then create your strategy and your credentials:

```bash
cp strategy.example.yaml strategy.yaml
cp .env.example .env
```

Both are gitignored — this repo is public, so your real strategy and your league
cookies stay on your machine.

## Usage

Print what your strategy will do, round by round:

```bash
uv run draftbot show
```

Run against a live draft:

```bash
uv run draftbot draft
```

## Status

Working: strategy schema and validation, the pick engine, tests.

Not built yet:

- **Platform adapters.** `platforms/base.py` defines the interface; no concrete
  adapter exists. Sleeper is the easiest first target — public read API, no OAuth.
- **Projections.** The engine falls back to ADP when `projected_points` is
  missing, so it works today but it's only as good as ADP until real projections
  are wired in.
- **Roster-slot awareness.** The engine counts positions but doesn't yet reason
  about FLEX or bench depth.

## Tests

```bash
uv run pytest
```
