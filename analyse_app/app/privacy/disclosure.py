"""Disclosure control (#646).

Runs TWICE: at the node before anything leaves it, and at the coordinator
after merging. Both, not either — a merge of two individually-safe partials
can produce an unsafe total, and a node cannot see what the other nodes sent.

The part that is easy to get wrong is SECONDARY SUPPRESSION. Blanking a cell
below the threshold is the obvious half and the useless half: if the table
publishes row and column totals, one suppressed cell in a row is recoverable
by subtraction. Suppressing it merely tells the reader where to look. See
``suppress_table``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: The brief's default. A deployment may raise it; see DisclosurePolicy.
DEFAULT_K_MIN = 5

#: Correlations need more than a mean does — a coefficient over a handful of
#: points is both unstable and unusually disclosive, since each point moves it
#: visibly.
DEFAULT_CORRELATION_MIN_N = 10

#: True min and max are never published: both are, by definition, one real
#: patient's value sitting at the edge of the distribution.
DEFAULT_PERCENTILE_RANGE = (5.0, 95.0)

#: What a suppressed value renders as.
SUPPRESSED = "<k"


class DisclosureError(ValueError):
    pass


@dataclass(frozen=True)
class DisclosurePolicy:
    """Thresholds for one run.

    ``k_min`` may be RAISED by a node whose organisation wants a stricter
    floor than the platform's. It can never be lowered below ``floor``: a
    coordinator must not be able to talk a node into disclosing more than the
    organisation that owns the data allows, which is the whole point of the
    node owning its policy.
    """
    k_min: int = DEFAULT_K_MIN
    floor: int = DEFAULT_K_MIN
    correlation_min_n: int = DEFAULT_CORRELATION_MIN_N
    percentile_range: tuple[float, float] = DEFAULT_PERCENTILE_RANGE
    round_to: int | None = None          # e.g. 5, for exports marked public

    def __post_init__(self):
        if self.k_min < self.floor:
            raise DisclosureError(
                f"k_min {self.k_min} is below the configured floor "
                f"{self.floor} — a policy may raise the threshold, never "
                f"lower it")
        if self.floor < 1:
            raise DisclosureError("floor must be at least 1")

    def stricter_of(self, other: "DisclosurePolicy") -> "DisclosurePolicy":
        """Combine policies by taking the STRICTER of each.

        Used when a coordinator merges partials from nodes with different
        policies. The merged result must satisfy every contributing node, so
        the highest k_min wins — never an average, and never the
        coordinator's own.
        """
        return DisclosurePolicy(
            k_min=max(self.k_min, other.k_min),
            floor=max(self.floor, other.floor),
            correlation_min_n=max(self.correlation_min_n,
                                  other.correlation_min_n),
            percentile_range=(max(self.percentile_range[0],
                                  other.percentile_range[0]),
                              min(self.percentile_range[1],
                                  other.percentile_range[1])),
            round_to=max(filter(None, (self.round_to, other.round_to)),
                         default=None),
        )


# ── counts ────────────────────────────────────────────────────────────

def suppress_counts(counts: Mapping[str, int],
                    policy: DisclosurePolicy) -> dict[str, Any]:
    """One-dimensional frequency suppression.

    With no margin published there is nothing to subtract from, so primary
    suppression alone is sufficient here. The moment a total is published
    alongside, use ``suppress_table``.
    """
    return {k: (SUPPRESSED if 0 < v < policy.k_min else v)
            for k, v in counts.items()}


@dataclass
class SuppressedTable:
    """A cross-tab after suppression, and why each cell went."""
    cells: list[list[Any]]
    rows: list[str]
    cols: list[str]
    primary: set[tuple[int, int]] = field(default_factory=set)
    secondary: set[tuple[int, int]] = field(default_factory=set)

    @property
    def suppressed(self) -> set[tuple[int, int]]:
        return self.primary | self.secondary


def suppress_table(table: Sequence[Sequence[int]],
                   policy: DisclosurePolicy,
                   *, rows: Sequence[str] | None = None,
                   cols: Sequence[str] | None = None,
                   publish_margins: bool = True) -> SuppressedTable:
    """Primary AND secondary suppression for a cross-tab.

    PRIMARY: every cell below ``k_min`` (and above zero — a true zero
    discloses nothing about an individual and hiding it loses real
    information).

    SECONDARY, which is the point of this function: when margins are
    published, a line containing exactly ONE suppressed cell leaks it —
    ``cell = total - sum(the rest)``. So every row and every column that has
    any suppression must have at least TWO suppressed cells. Complements are
    chosen smallest-first, to give away as little as possible, and the sweep
    repeats because suppressing a complement in a row can leave its column
    with exactly one.

    A line too short to hold a second suppression (a single-cell row) cannot
    be protected this way; the whole line is suppressed instead.
    """
    grid = [list(r) for r in table]
    n_rows, n_cols = len(grid), (len(grid[0]) if grid else 0)
    rows = list(rows or [f"r{i}" for i in range(n_rows)])
    cols = list(cols or [f"c{j}" for j in range(n_cols)])

    primary = {(i, j) for i in range(n_rows) for j in range(n_cols)
               if 0 < grid[i][j] < policy.k_min}
    secondary: set[tuple[int, int]] = set()

    if publish_margins:

        def _cost(cell, marked, *, along_row: bool):
            """Rank a candidate complement. Lower is better.

            The first term is what stops the cascade. A complement chosen in
            a column that ALREADY holds a suppressed cell satisfies the row
            and the column at once; one chosen in a clean column leaves that
            column with exactly one suppression, which the next sweep must
            then fix, which can leave another line with one, and so on until
            the table is empty. Naively picking the smallest cell does
            exactly that. The second term is the information actually given
            up, so among equally cascade-free choices the smallest count
            goes.
            """
            i, j = cell
            cross = ([(r, j) for r in range(n_rows)] if along_row
                     else [(i, c) for c in range(n_cols)])
            already = any(c in marked for c in cross if c != cell)
            return (0 if already else 1, grid[i][j])

        changed = True
        while changed:
            changed = False

            for i in range(n_rows):                      # rows
                marked = primary | secondary
                line = [(i, j) for j in range(n_cols)]
                hidden = [c for c in line if c in marked]
                if len(hidden) != 1:
                    continue
                if len(line) < 2:
                    if hidden[0] not in secondary:       # degenerate line
                        secondary.add(hidden[0])
                        changed = True
                    continue
                cand = [c for c in line if c not in marked]
                if cand:
                    secondary.add(min(
                        cand, key=lambda c: _cost(c, marked, along_row=True)))
                    changed = True

            for j in range(n_cols):                      # columns
                marked = primary | secondary
                line = [(i, j) for i in range(n_rows)]
                hidden = [c for c in line if c in marked]
                if len(hidden) != 1:
                    continue
                if len(line) < 2:
                    if hidden[0] not in secondary:
                        secondary.add(hidden[0])
                        changed = True
                    continue
                cand = [c for c in line if c not in marked]
                if cand:
                    secondary.add(min(
                        cand, key=lambda c: _cost(c, marked, along_row=False)))
                    changed = True

    hidden = primary | secondary
    cells = [[SUPPRESSED if (i, j) in hidden else grid[i][j]
              for j in range(n_cols)] for i in range(n_rows)]
    return SuppressedTable(cells=cells, rows=rows, cols=cols,
                           primary=primary, secondary=secondary)


# ── summary statistics ────────────────────────────────────────────────

def safe_summary(n: int, mean: float | None, sd: float | None,
                 policy: DisclosurePolicy) -> dict[str, Any]:
    """Mean and SD only at or above ``k_min``.

    At n = 1 the mean IS the patient's value. At n = 2 either value is
    recoverable from the mean and SD together.
    """
    if n < policy.k_min:
        return {"n": SUPPRESSED, "mean": SUPPRESSED, "sd": SUPPRESSED,
                "suppressed": True}
    return {"n": n, "mean": mean, "sd": sd, "suppressed": False}


def safe_correlation(n: int, r: float | None,
                     policy: DisclosurePolicy) -> dict[str, Any]:
    if n < policy.correlation_min_n:
        return {"n": SUPPRESSED, "r": SUPPRESSED, "suppressed": True,
                "reason": f"n below {policy.correlation_min_n}"}
    return {"n": n, "r": r, "suppressed": False}


def safe_group_comparison(group_ns: Mapping[str, int],
                          policy: DisclosurePolicy) -> dict[str, Any]:
    """A comparison is released only when EVERY group meets the threshold.

    Releasing the groups that pass would disclose the one that did not, by
    difference from a published total.
    """
    small = sorted(g for g, n in group_ns.items() if n < policy.k_min)
    if small:
        return {"suppressed": True,
                "reason": "a group is below the minimum cell size",
                "groups_below_threshold": len(small)}
    return {"suppressed": False}


def safe_range(values: Sequence[float],
               policy: DisclosurePolicy) -> tuple[float, float] | None:
    """A percentile range instead of true min and max.

    The true extremes are each one identifiable patient sitting at the edge
    of the distribution — the single most disclosive pair of numbers a
    summary can carry.
    """
    if len(values) < policy.k_min:
        return None
    ordered = sorted(values)
    lo, hi = policy.percentile_range

    def pct(p: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        k = (len(ordered) - 1) * (p / 100.0)
        f, c = math.floor(k), math.ceil(k)
        if f == c:
            return ordered[int(k)]
        return ordered[f] * (c - k) + ordered[c] * (k - f)

    return pct(lo), pct(hi)


def merge_small_bins(bins: Sequence[tuple[Any, int]],
                     policy: DisclosurePolicy) -> list[tuple[Any, int]]:
    """Histogram bins below ``k_min`` merge into their neighbour.

    Merging rather than dropping: a dropped bin changes the shape of the
    distribution silently, while a merged one keeps every patient in the
    picture at coarser resolution. A trailing small bin merges backwards, so
    no patient is lost at either end.
    """
    out: list[list[Any]] = []
    for label, count in bins:
        if out and 0 < count < policy.k_min:
            out[-1][1] += count
            out[-1][0] = f"{out[-1][0]}+{label}"
        else:
            out.append([label, count])
    while len(out) > 1 and 0 < out[-1][1] < policy.k_min:
        tail = out.pop()
        out[-1][1] += tail[1]
        out[-1][0] = f"{out[-1][0]}+{tail[0]}"
    return [(lbl, cnt) for lbl, cnt in out]


def round_for_public(value: int | float, policy: DisclosurePolicy):
    """Optional rounding for exports marked public."""
    if policy.round_to is None or not isinstance(value, (int, float)):
        return value
    step = policy.round_to
    return int(round(value / step) * step)


# ── differencing protection ───────────────────────────────────────────

#: What a refusal does. Advisory lets an analyst proceed with a warning
#: recorded; hard refuses. Advisory is the DEFAULT because a false positive
#: here blocks legitimate work — two cohorts can differ by four patients for
#: entirely innocent reasons — and a warning that is recorded is still
#: evidence if a pattern emerges. An organisation that wants hard sets it.
ADVISORY = "advisory"
HARD = "hard"


@dataclass
class DifferencingGuard:
    """Per-user history of cohort membership, to catch subtraction attacks.

    Two individually-legal queries whose cohorts differ by one patient
    disclose that patient by difference. Neither query looks wrong on its
    own; only the pair does, which is why this needs history rather than a
    per-query rule.

    The hook and the basic check live here; AN-18 hardens it (across
    sessions, across saved recipes rerun on fresh data, and whether a block
    is advisory or hard).
    """
    policy: DisclosurePolicy
    mode: str = ADVISORY
    history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: Every near miss, for the admin view. An attacker probing repeatedly is
    #: visible as a PATTERN across attempts, never in any single one — so the
    #: record has to outlive the attempt that produced it.
    near_misses: list[dict[str, Any]] = field(default_factory=list)

    def check(self, user_guid: str, cohort: Iterable[str], *,
              recipe_id: str | None = None,
              session_id: str | None = None) -> dict[str, Any]:
        """Compare against this user's earlier cohorts.

        #661 hardening, three parts:

        - History is keyed by USER and persists ACROSS SESSIONS. Keying it by
          session would make logging out and back in the whole attack.
        - A cohort submitted under the SAME recipe_id is a rerun on fresh
          data, not a probe: a saved recipe run monthly will legitimately
          differ by a patient or two each time, and treating that as an
          attack would make recipes unusable — which is the brief's own
          feature.
        - A refused attempt is RECORDED either way, and lands in
          ``near_misses`` for the admin view.
        """
        new = frozenset(cohort)
        prior = self.history.setdefault(user_guid, [])

        for entry in prior:
            if recipe_id is not None and entry.get("recipe_id") == recipe_id:
                continue                      # a rerun, not a probe
            diff = len(new.symmetric_difference(entry["cohort"]))
            if 0 < diff < self.policy.k_min:
                record = {"user_guid": user_guid, "difference": diff,
                          "recipe_id": recipe_id, "session_id": session_id,
                          "mode": self.mode}
                self.near_misses.append(record)
                prior.append({"cohort": new, "recipe_id": recipe_id,
                              "session_id": session_id})
                return {
                    "allowed": self.mode == ADVISORY,
                    "warned": True,
                    "mode": self.mode,
                    "reason": ("this cohort differs from an earlier one by "
                               "fewer patients than the minimum cell size, "
                               "which would disclose them by subtraction"),
                    "difference": diff,
                }
        prior.append({"cohort": new, "recipe_id": recipe_id,
                      "session_id": session_id})
        return {"allowed": True, "warned": False, "mode": self.mode}

    def admin_view(self) -> list[dict[str, Any]]:
        """Near misses, for an administrator. Carries no cohort membership —
        an admin screen about disclosure risk must not itself be a place
        where cohorts can be read."""
        return [dict(m) for m in self.near_misses]
