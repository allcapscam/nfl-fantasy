"""Score a projected stat line under a league's own rules.

Sleeper ships precomputed totals -- `pts_std`, `pts_half_ppr`, `pts_ppr` -- and
using one of them means drafting under *Sleeper's* idea of half-PPR rather than
your league's. Usually those agree. When they do not, nothing says so: the
number is a plausible total either way, and every downstream figure inherits the
error silently.

So the raw stat line is scored directly. Sleeper's projections carry the same
stat keys its league `scoring_settings` use -- `pass_yd`, `rec`, `fum_lost` --
so a league's scoring is a dict from those keys to points per unit, and a
player's total is the dot product with his projected stats.

**The key names are the risk**, not the arithmetic. A key spelled wrong
contributes nothing and quietly shrinks every total that depends on it. That is
why `reconcile` exists: scoring every player with the Sleeper table below must
reproduce Sleeper's own `pts_half_ppr`. If it does not, the names are wrong and
the caller is expected to stop rather than draft off the result.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

#: Sleeper's default half-PPR scoring, as stat key -> points per unit.
#:
#: This exists to be *checked*, not trusted: `reconcile` scores real projections
#: with it and compares against Sleeper's own `pts_half_ppr`. A mismatch means
#: this table is wrong -- a renamed key, a changed default -- and says so
#: loudly. It is the calibration weight, not an assumption.
SLEEPER_HALF_PPR: dict[str, float] = {
    # Passing
    "pass_yd": 0.04,
    "pass_td": 4.0,
    "pass_int": -1.0,
    "pass_2pt": 2.0,
    # Rushing
    "rush_yd": 0.1,
    "rush_td": 6.0,
    "rush_2pt": 2.0,
    # Receiving
    "rec": 0.5,
    "rec_yd": 0.1,
    "rec_td": 6.0,
    "rec_2pt": 2.0,
    # Loose ball
    "fum_lost": -2.0,
    "fum_rec_td": 6.0,
    # Return touchdowns scored by a skill player
    "st_td": 6.0,
    # Kicking
    "xpm": 1.0,
    "xpmiss": -1.0,
    "fgm_0_19": 3.0,
    "fgm_20_29": 3.0,
    "fgm_30_39": 3.0,
    "fgm_40_49": 4.0,
    "fgm_50p": 5.0,
    "fgmiss": -1.0,
    # Defence / special teams
    "sack": 1.0,
    "int": 2.0,
    "fum_rec": 2.0,
    "safe": 2.0,
    "blk_kick": 2.0,
    "def_td": 6.0,
    "def_st_td": 6.0,
    "pts_allow_0": 10.0,
    "pts_allow_1_6": 7.0,
    "pts_allow_7_13": 4.0,
    "pts_allow_14_20": 1.0,
    "pts_allow_21_27": 0.0,
    "pts_allow_28_34": -1.0,
    "pts_allow_35p": -4.0,
}


def score_line(stats: Mapping[str, object], table: Mapping[str, float]) -> float:
    """Points a projected stat line is worth under `table`.

    Only keys the table names contribute, so a stat the league does not score
    is ignored rather than guessed at.
    """
    total = 0.0
    for key, points in table.items():
        value = stats.get(key)
        if value in (None, ""):
            continue
        try:
            total += float(value) * points
        except (TypeError, ValueError):
            continue
    return total


@dataclass(frozen=True)
class Reconciliation:
    """How closely a table reproduces a total the platform computed itself."""

    checked: int
    within_tolerance: int
    mean_error: float
    worst_error: float
    worst_player: str | None
    missing_keys: list[str]

    @property
    def agreement(self) -> float:
        return self.within_tolerance / self.checked if self.checked else 0.0

    def ok(self, threshold: float = 0.98) -> bool:
        """Do the stat keys reproduce the platform's own arithmetic?

        Agreement is the test, not `missing_keys`. A wholesale naming error
        collapses agreement, which is what has to be caught; a single key the
        projections happen not to carry -- Sleeper does not project every rule
        it scores -- is worth reporting but is not grounds to refuse a board.
        """
        return bool(self.checked) and self.agreement >= threshold

    def describe(self) -> str:
        if not self.checked:
            return "nothing to reconcile: no player carried a reference total."
        lines = [
            f"reconciled {self.checked} players against Sleeper's own total: "
            f"{self.agreement:.1%} agree, mean error {self.mean_error:.2f} pts, "
            f"worst {self.worst_error:.2f}"
            + (f" ({self.worst_player})" if self.worst_player else "")
        ]
        if self.missing_keys:
            lines.append(
                "  stat keys named by the table but absent from every projection: "
                + ", ".join(sorted(self.missing_keys))
            )
        return "\n".join(lines)


def reconcile(
    lines: Iterable[tuple[str, Mapping[str, object]]],
    reference_key: str = "pts_half_ppr",
    table: Mapping[str, float] | None = None,
    tolerance: float = 0.5,
) -> Reconciliation:
    """Check that `table` reproduces the platform's own precomputed total.

    This is the guard on the whole approach. Scoring from raw stats is only
    safe if the stat keys are the ones the feed actually uses, and the cheapest
    proof of that is arithmetic the platform already did: score every player
    with Sleeper's own rules and see whether Sleeper's number comes back.

    `missing_keys` catches the failure that agreement alone would not -- a key
    nobody has, contributing zero to every player, so the totals still match
    while a whole scoring rule is silently inert.
    """
    table = SLEEPER_HALF_PPR if table is None else table
    checked = within = 0
    total_error = worst = 0.0
    worst_player: str | None = None
    seen: set[str] = set()

    for name, stats in lines:
        seen.update(k for k, v in stats.items() if v not in (None, ""))
        reference = stats.get(reference_key)
        if reference in (None, ""):
            continue
        try:
            expected = float(reference)
        except (TypeError, ValueError):
            continue
        error = abs(score_line(stats, table) - expected)
        checked += 1
        total_error += error
        if error <= tolerance:
            within += 1
        if error > worst:
            worst, worst_player = error, name

    return Reconciliation(
        checked=checked,
        within_tolerance=within,
        mean_error=total_error / checked if checked else 0.0,
        worst_error=worst,
        worst_player=worst_player,
        missing_keys=[k for k in table if k not in seen],
    )


def unused_keys(
    lines: Iterable[tuple[str, Mapping[str, object]]], table: Mapping[str, float]
) -> list[str]:
    """Rules in `table` that no projection can pay out, because no player has
    the stat.

    Not an error -- a feed projects yards and touchdowns far more often than it
    projects missed kicks -- but it means those rules are inert, so a league
    that leans on one is not being scored the way it plays.
    """
    seen: set[str] = set()
    for _name, stats in lines:
        seen.update(k for k, v in stats.items() if v not in (None, ""))
    return sorted(k for k in table if k not in seen)


def compare_tables(
    lines: Iterable[tuple[str, str, Mapping[str, object]]],
    ours: Mapping[str, float],
    theirs: Mapping[str, float] | None = None,
) -> dict[str, tuple[int, float, float]]:
    """Per position, how far our scoring lands from the platform's.

    Returns `position -> (players, mean signed difference, largest absolute)`.
    A near-zero row means the precomputed total would have been fine for that
    position; a large one is the reason for scoring from raw stats at all.
    """
    theirs = SLEEPER_HALF_PPR if theirs is None else theirs
    buckets: dict[str, list[float]] = {}
    for _name, position, stats in lines:
        buckets.setdefault(position, []).append(
            score_line(stats, ours) - score_line(stats, theirs)
        )
    return {
        position: (len(diffs), sum(diffs) / len(diffs), max(diffs, key=abs))
        for position, diffs in sorted(buckets.items())
        if diffs
    }
