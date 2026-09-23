"""Histograms (#647).

Bin edges are fixed by the COORDINATOR, never by each node. If nodes chose
their own bins from their own data, the counts would not be addable and the
merged histogram would be a picture of nothing. When the spec does not state
bins, the coordinator takes a first pass over the nodes' sketches to pick a
range, then sends the edges down — which is why data-driven bins cost a round
trip.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from app.privacy.disclosure import DisclosurePolicy, merge_small_bins

from .base import EXACT, Partial, Result, _merge_by_source
from .sketch import TDigest

KIND = "histogram"


def edges_from(low: float, high: float, width: float) -> list[float]:
    if width <= 0:
        raise ValueError("bin width must be positive")
    if high <= low:
        raise ValueError("bin range must ascend")
    n = int(math.ceil((high - low) / width))
    return [low + i * width for i in range(n + 1)]


def propose_edges(sketches: Sequence[TDigest], width: float,
                  *, lo_q: float = 0.01, hi_q: float = 0.99) -> list[float]:
    """The coordinator's first pass.

    Uses inner quantiles rather than min and max: the extremes are single
    patients (AN-3 refuses to publish them), and a range stretched to an
    outlier produces a histogram that is all empty bins and one spike.
    """
    merged = TDigest.merged(list(sketches))
    lo, hi = merged.quantile(lo_q), merged.quantile(hi_q)
    if lo is None or hi is None:
        raise ValueError("no data to propose bins from")
    if hi <= lo:
        hi = lo + width
    return edges_from(math.floor(lo / width) * width,
                      math.ceil(hi / width) * width, width)


def local(values: Iterable[Any], edges: Sequence[float], *,
          source: str | None = None) -> Partial:
    edges = list(edges)
    counts = [0] * (len(edges) - 1)
    under = over = missing = 0
    for v in values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            missing += 1
            continue
        x = float(v)
        if x < edges[0]:
            under += 1
        elif x >= edges[-1]:
            over += 1
        else:
            # linear scan is fine: bin counts are small and fixed
            for i in range(len(counts)):
                if edges[i] <= x < edges[i + 1]:
                    counts[i] += 1
                    break
    return Partial(kind=KIND, source=source, data={
        "edges": edges, "counts": counts,
        "under": under, "over": over, "missing": missing})


def merge(partials: Sequence[Partial]) -> Partial:
    parts = list(partials)
    edges = parts[0].data["edges"]
    for p in parts:
        if p.data["edges"] != edges:
            raise ValueError(
                "histogram merge: nodes used different bin edges — the "
                "coordinator must fix them before the local pass")
    counts = [0] * len(parts[0].data["counts"])
    for p in parts:
        for i, c in enumerate(p.data["counts"]):
            counts[i] += c
    return Partial(kind=KIND, by_source=_merge_by_source(parts), data={
        "edges": edges, "counts": counts,
        "under": sum(p.data["under"] for p in parts),
        "over": sum(p.data["over"] for p in parts),
        "missing": sum(p.data["missing"] for p in parts)})


def finalize(partial: Partial, policy: DisclosurePolicy) -> Result:
    d = partial.data
    edges, counts = d["edges"], d["counts"]
    labelled = [(f"{edges[i]:g}–{edges[i + 1]:g}", counts[i])
                for i in range(len(counts))]
    merged = merge_small_bins(labelled, policy)
    notes = []
    if len(merged) != len(labelled):
        notes.append("Some bins were combined so that no bin describes fewer "
                     "patients than the minimum group size.")
    if d["missing"]:
        notes.append(f"{d['missing']} observations had no recorded value.")
    if d["under"] or d["over"]:
        notes.append(f"{d['under'] + d['over']} values fell outside the "
                     f"chosen range and are counted separately.")
    return Result(kind=KIND, exactness=EXACT, by_source=partial.by_source,
                  notes=notes, pooled={
                      "bins": [{"label": l, "count": c} for l, c in merged],
                      "outside_range": d["under"] + d["over"],
                      "missing": d["missing"]})
