"""Deciding who is IN the cohort (#696 / AN-13).

Until this existed, `spec.cohort.include` was **never read by anything on the
node path**. `run_spec()` took an already-resolved list of patient guids and
computed over all of them, so a spec that said "TBSA >= 5" was, at the node, a
spec that said nothing — and the figure came back labelled as though the
criterion had been applied.

That is the worst kind of wrong: not an error, a *wider population* than the
analyst asked about, reported under their question.

## Semantics, stated because they are a choice

- **A criterion is satisfied if ANY of the patient's observations of that
  concept satisfies it.** "Ever had TBSA >= 5" is how an inclusion criterion
  reads clinically. The alternative — test the aggregated value — would make
  membership depend on the aggregation chosen for a *variable*, so the same
  cohort would change meaning when someone edited an unrelated part of the
  spec.
- **Several criteria are ANDed.** The field is `include`; a patient must meet
  all of them.
- **A criterion this node cannot evaluate is an ERROR, never a pass.**
  Skipping one silently is exactly the failure above, wearing a different hat.

## What this does NOT do

It does not narrow what is *read*. Rows reach it after spärr, after the
consent-filtered read and after projection, and it decides membership from
them. Narrowing the read itself needs the CDR to answer "which of your
patients match this", which it cannot today — see the cdr-side ticket. That
is an efficiency question; this is the correctness one.
"""
from __future__ import annotations

import operator
from typing import Any, Iterable

from app.spec import AnalysisSpec
from app.spec.models import AgeCriterion, ObservationCriterion

_OPS = {
    ">=": operator.ge, ">": operator.gt,
    "<=": operator.le, "<": operator.lt,
    "==": operator.eq, "!=": operator.ne,
}


class CohortCriteriaError(RuntimeError):
    """A criterion could not be evaluated. Raised, never skipped."""


def _numeric(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _satisfies(row: dict, crit: ObservationCriterion) -> bool:
    if row.get("concept") != crit.observation:
        return False
    val = _numeric(row.get("value"))
    if val is None:
        # A non-numeric value for a numeric predicate is not a match. It is
        # also not an error: a concept can legitimately carry text for some
        # patients and numbers for others.
        return False
    return _OPS[crit.op.value](val, crit.value)


def _check_evaluable(rows: list[dict], spec: AnalysisSpec) -> None:
    """Refuse before filtering, so a node never reports a cohort it guessed."""
    for crit in spec.cohort.include:
        if isinstance(crit, AgeCriterion):
            raise CohortCriteriaError(
                "this spec selects an age band, and age is not projected into "
                "the rows this node reads — the cohort cannot be established "
                "here. Refusing rather than ignoring the criterion, which "
                "would compute over every age.")
        if not isinstance(crit, ObservationCriterion):
            raise CohortCriteriaError(
                f"unsupported cohort criterion: {type(crit).__name__}")

    # `is not None`, not `in`: projection fills every allowlisted field, so
    # `"concept" in row` is true even when the source never supplied one. The
    # first version of this guard tested membership and therefore never
    # fired — the cohort came back empty instead, which reads as "nobody
    # qualifies" rather than "this node cannot tell".
    if rows and not any(r.get("concept") is not None for r in rows):
        raise CohortCriteriaError(
            "the rows read at this node carry no 'concept', so cohort "
            "criteria naming one cannot be evaluated. Refusing rather than "
            "reporting an empty cohort, which would read as 'nobody "
            "qualifies' instead of 'this node cannot tell'")


def members(rows: Iterable[dict], spec: AnalysisSpec) -> set[str]:
    """The pids that satisfy EVERY criterion."""
    rows = list(rows)
    _check_evaluable(rows, spec)

    by_crit: list[set[str]] = []
    for crit in spec.cohort.include:
        by_crit.append({r["pid"] for r in rows
                        if r.get("pid") and _satisfies(r, crit)})
    if not by_crit:
        return {r["pid"] for r in rows if r.get("pid")}
    return set.intersection(*by_crit)


def apply(rows: Iterable[dict], spec: AnalysisSpec) -> tuple[list[dict], int]:
    """(rows for cohort members, number of patients excluded).

    The count is returned so the node can report it. A cohort that turns out
    smaller than the candidate list is normal and must be visible; a cohort
    that is silently the candidate list is the bug this module exists to fix.
    """
    rows = list(rows)
    keep = members(rows, spec)
    seen = {r["pid"] for r in rows if r.get("pid")}
    return [r for r in rows if r.get("pid") in keep], len(seen - keep)
