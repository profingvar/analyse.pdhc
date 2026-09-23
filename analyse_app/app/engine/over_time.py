"""Change over time since the index event (#652).

Bins are days since the index event, never calendar dates — AN-2 coarsens
away the calendar before this sees anything, and a curve indexed on wall-clock
time would leak the one thing the offset was designed to remove.

Each bin carries n, Σx and Σx², so the mean and its CI per bin are exact
across sources.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from app.privacy.disclosure import DisclosurePolicy, safe_summary

from .base import EXACT, Partial, Result, _merge_by_source

KIND = "over_time"


def local(points: Sequence[tuple[int, float]], *, bin_days: int,
          range_days: tuple[int, int], source: str | None = None) -> Partial:
    lo, hi = range_days
    n_bins = max(1, math.ceil((hi - lo) / bin_days))
    bins = [{"n": 0, "sum": 0.0, "sumsq": 0.0} for _ in range(n_bins)]
    outside = 0
    for day, value in points:
        if value is None or day is None:
            continue
        if not (lo <= day < hi):
            outside += 1
            continue
        idx = min(int((day - lo) // bin_days), n_bins - 1)
        b = bins[idx]
        b["n"] += 1
        b["sum"] += float(value)
        b["sumsq"] += float(value) ** 2
    return Partial(kind=KIND, source=source, data={
        "bin_days": bin_days, "range_days": [lo, hi],
        "bins": bins, "outside": outside})


def merge(partials: Sequence[Partial]) -> Partial:
    parts = list(partials)
    base = parts[0].data
    for p in parts:
        if p.data["bin_days"] != base["bin_days"] or \
                p.data["range_days"] != base["range_days"]:
            raise ValueError(
                "over_time merge: nodes used different bins — the coordinator "
                "fixes them from the spec, so this means a stale node")
    bins = [{"n": 0, "sum": 0.0, "sumsq": 0.0}
            for _ in range(len(base["bins"]))]
    for p in parts:
        for i, b in enumerate(p.data["bins"]):
            bins[i]["n"] += b["n"]
            bins[i]["sum"] += b["sum"]
            bins[i]["sumsq"] += b["sumsq"]
    return Partial(kind=KIND, by_source=_merge_by_source(parts), data={
        "bin_days": base["bin_days"], "range_days": base["range_days"],
        "bins": bins, "outside": sum(p.data["outside"] for p in parts)})


def finalize(partial: Partial, policy: DisclosurePolicy) -> Result:
    d = partial.data
    lo, step = d["range_days"][0], d["bin_days"]
    out = []
    hidden = 0
    for i, b in enumerate(d["bins"]):
        n = b["n"]
        label = f"day {lo + i * step}–{lo + (i + 1) * step - 1}"
        if n == 0:
            out.append({"label": label, "n": 0, "mean": None, "ci95": None})
            continue
        mean = b["sum"] / n
        safe = safe_summary(n, mean, None, policy)
        if safe["suppressed"]:
            hidden += 1
            out.append({"label": label, "n": "<k", "mean": "<k",
                        "ci95": None})
            continue
        var = max((b["sumsq"] - n * mean * mean) / (n - 1), 0.0) if n > 1 else 0.0
        sd = math.sqrt(var)
        ci = None
        if n > 1 and sd > 0:
            from scipy import stats as _st
            half = _st.t.ppf(0.975, n - 1) * sd / math.sqrt(n)
            ci = [mean - half, mean + half]
        out.append({"label": label, "n": n, "mean": mean, "ci95": ci})

    notes = ["Time is counted in days from each patient's own index event, "
             "not by calendar date."]
    if hidden:
        notes.append(f"{hidden} time periods are hidden because too few "
                     f"patients had a measurement in them.")
    if d["outside"]:
        notes.append(f"{d['outside']} measurements fell outside the chosen "
                     f"time range and are not shown.")
    return Result(kind=KIND, exactness=EXACT, by_source=partial.by_source,
                  notes=notes, pooled={"bins": out})
