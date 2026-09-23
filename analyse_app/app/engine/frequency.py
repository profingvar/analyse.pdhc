"""Frequency tables and cross-tabs (#647). Cell counts merge exactly."""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from app.privacy.disclosure import (
    DisclosurePolicy, suppress_counts, suppress_table,
)

from .base import EXACT, Partial, Result, _merge_by_source

KIND = "frequency"


def local(values: Iterable[Any], *, source: str | None = None,
          cross: Iterable[Any] | None = None) -> Partial:
    if cross is None:
        counts: dict[str, int] = {}
        for v in values:
            key = "(missing)" if v is None else str(v)
            counts[key] = counts.get(key, 0) + 1
        return Partial(kind=KIND, source=source,
                       data={"cross": False, "counts": counts})

    cells: dict[str, dict[str, int]] = {}
    for a, b in zip(values, cross):
        ra = "(missing)" if a is None else str(a)
        cb = "(missing)" if b is None else str(b)
        cells.setdefault(ra, {})
        cells[ra][cb] = cells[ra].get(cb, 0) + 1
    return Partial(kind=KIND, source=source,
                   data={"cross": True, "cells": cells})


def merge(partials: Sequence[Partial]) -> Partial:
    parts = list(partials)
    by_source = _merge_by_source(parts)
    if not parts[0].data.get("cross"):
        counts: dict[str, int] = {}
        for p in parts:
            for k, v in p.data["counts"].items():
                counts[k] = counts.get(k, 0) + v
        return Partial(kind=KIND, by_source=by_source,
                       data={"cross": False, "counts": counts})

    cells: dict[str, dict[str, int]] = {}
    for p in parts:
        for r, row in p.data["cells"].items():
            cells.setdefault(r, {})
            for c, v in row.items():
                cells[r][c] = cells[r].get(c, 0) + v
    return Partial(kind=KIND, by_source=by_source,
                   data={"cross": True, "cells": cells})


def finalize(partial: Partial, policy: DisclosurePolicy) -> Result:
    d = partial.data
    if not d.get("cross"):
        return Result(kind=KIND, exactness=EXACT, by_source=partial.by_source,
                      pooled={"counts": suppress_counts(d["counts"], policy)})

    rows = sorted(d["cells"])
    cols = sorted({c for row in d["cells"].values() for c in row})
    grid = [[d["cells"][r].get(c, 0) for c in cols] for r in rows]
    table = suppress_table(grid, policy, rows=rows, cols=cols)

    notes = []
    if table.suppressed:
        notes.append(
            f"{len(table.suppressed)} cells are hidden to protect small "
            f"groups; some are hidden only so the others cannot be worked "
            f"out from the totals.")
    chi = _chi_square(grid)
    if chi is not None:
        notes.append("Chi-square is exact across sources; it describes how "
                     "far the table is from independence, not an effect.")
    return Result(kind=KIND, exactness=EXACT, by_source=partial.by_source,
                  notes=notes, pooled={
                      "rows": table.rows, "cols": table.cols,
                      "cells": table.cells, "chi_square": chi})


def _chi_square(grid) -> dict[str, Any] | None:
    n = sum(sum(r) for r in grid)
    if n == 0 or len(grid) < 2 or len(grid[0]) < 2:
        return None
    row_t = [sum(r) for r in grid]
    col_t = [sum(r[j] for r in grid) for j in range(len(grid[0]))]
    stat = 0.0
    for i, r in enumerate(grid):
        for j, obs in enumerate(r):
            exp = row_t[i] * col_t[j] / n
            if exp > 0:
                stat += (obs - exp) ** 2 / exp
    dof = (len(grid) - 1) * (len(grid[0]) - 1)
    from scipy import stats as _st
    return {"statistic": stat, "dof": dof,
            "p_value": float(_st.chi2.sf(stat, dof))}
