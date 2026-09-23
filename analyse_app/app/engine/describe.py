"""Describe — continuous and categorical (#647).

Continuous: the node returns n, missing, Σx, Σx² and a quantile sketch.
Mean, SD and the CI are EXACT when merged, because they are functions of
sums. Median and IQR are APPROXIMATE across nodes, because order statistics
are not.

Missing is carried explicitly at every stage. A mean over 40 of 200 patients
and a mean over 200 of 200 are different claims, and a result that reports
only the first number invites the reader to believe the second.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from app.privacy.disclosure import DisclosurePolicy, safe_summary

from .base import APPROXIMATE, EXACT, Partial, Result, _merge_by_source
from .sketch import TDigest

KIND = "describe"


def local(values: Iterable[Any], *, source: str | None = None,
          categorical: bool = False, k_min: int = 5) -> Partial:
    vals = list(values)
    if categorical:
        counts: dict[str, int] = {}
        missing = 0
        for v in vals:
            if v is None:
                missing += 1
            else:
                counts[str(v)] = counts.get(str(v), 0) + 1
        data = {"categorical": True, "counts": counts, "missing": missing,
                "n": sum(counts.values())}
        return Partial(kind=KIND, data=data, source=source)

    digest = TDigest()
    n = 0
    missing = 0
    total = 0.0
    total_sq = 0.0
    for v in vals:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            missing += 1
            continue
        x = float(v)
        n += 1
        total += x
        total_sq += x * x
        digest.add(x)
    digest.compress()
    # A centroid of weight 1 is one patient's exact value, and this partial
    # crosses the wire. Protect the sketch BEFORE it leaves the node — the
    # patients at the extremes are both the ones a small centroid describes
    # and the ones most easily identified.
    return Partial(kind=KIND, source=source, data={
        "categorical": False, "n": n, "missing": missing,
        "sum": total, "sumsq": total_sq,
        "digest": digest.protect(k_min).to_json(),
    })


def merge(partials: Sequence[Partial]) -> Partial:
    parts = list(partials)
    if not parts:
        raise ValueError("merge: nothing to merge")
    by_source = _merge_by_source(parts)

    if parts[0].data.get("categorical"):
        counts: dict[str, int] = {}
        missing = 0
        for p in parts:
            for k, v in p.data["counts"].items():
                counts[k] = counts.get(k, 0) + v
            missing += p.data["missing"]
        return Partial(kind=KIND, by_source=by_source, data={
            "categorical": True, "counts": counts, "missing": missing,
            "n": sum(counts.values())})

    digest = TDigest.merged(
        [TDigest.from_json(p.data["digest"]) for p in parts])
    return Partial(kind=KIND, by_source=by_source, data={
        "categorical": False,
        "n": sum(p.data["n"] for p in parts),
        "missing": sum(p.data["missing"] for p in parts),
        "sum": sum(p.data["sum"] for p in parts),
        "sumsq": sum(p.data["sumsq"] for p in parts),
        "digest": digest.to_json(),
        "n_sources": len(parts),
    })


def _stats(d: dict[str, Any]) -> dict[str, Any]:
    n, total, total_sq = d["n"], d["sum"], d["sumsq"]
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "ci95": None}
    mean = total / n
    if n < 2:
        return {"n": n, "mean": mean, "sd": None, "ci95": None}
    # population sum of squares -> sample variance
    var = max((total_sq - n * mean * mean) / (n - 1), 0.0)
    sd = math.sqrt(var)
    from scipy import stats as _st
    half = _st.t.ppf(0.975, n - 1) * (sd / math.sqrt(n)) if sd > 0 else 0.0
    return {"n": n, "mean": mean, "sd": sd,
            "ci95": [mean - half, mean + half]}


def finalize(partial: Partial, policy: DisclosurePolicy) -> Result:
    d = partial.data
    notes: list[str] = []

    if d.get("categorical"):
        from app.privacy.disclosure import suppress_counts
        pooled = {"counts": suppress_counts(d["counts"], policy),
                  "missing": d["missing"], "n": d["n"]}
        if d["missing"]:
            notes.append(f"{d['missing']} observations had no recorded value.")
        return Result(kind=KIND, pooled=pooled, by_source=partial.by_source,
                      exactness=EXACT, notes=notes)

    st = _stats(d)
    safe = safe_summary(st["n"], st["mean"], st["sd"], policy)
    pooled: dict[str, Any] = dict(safe)
    if not safe["suppressed"]:
        pooled["ci95"] = st["ci95"]
        digest = TDigest.from_json(d["digest"])
        pooled["median"] = digest.quantile(0.5)
        pooled["iqr"] = [digest.quantile(0.25), digest.quantile(0.75)]
    pooled["missing"] = d["missing"]

    # Exactness is a property of the FEDERATION, not of the maths: on a single
    # node the quantiles come from that node's own sketch and are still
    # approximate, but nothing is lost to merging. Say which is which.
    multi = d.get("n_sources", 1) > 1
    exactness = APPROXIMATE if multi else EXACT
    if not safe["suppressed"]:
        notes.append(
            "Mean, standard deviation and confidence interval are exact. "
            "Median and IQR come from a bounded sketch and are approximate"
            + (" across sources." if multi else "."))
    if d["missing"]:
        notes.append(f"{d['missing']} observations had no recorded value.")
    return Result(kind=KIND, pooled=pooled, by_source=partial.by_source,
                  exactness=exactness, notes=notes)
