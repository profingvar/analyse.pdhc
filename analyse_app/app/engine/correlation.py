"""Correlation (#647).

Pearson is exactly federatable: it is a function of n, Σx, Σy, Σx², Σy² and
Σxy, all of which add. Pairwise-complete, so a patient missing one variable
still contributes to the pairs they do have.

Spearman is NOT. Ranks depend on the whole distribution, so a node can only
rank within itself. On a single node the result is exact; across nodes it is
an average of per-node coefficients and is flagged approximate, loudly,
because a reader cannot tell by looking.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from app.privacy.disclosure import DisclosurePolicy, safe_correlation

from .base import APPROXIMATE, EXACT, Partial, Result, _merge_by_source

KIND = "correlation"


def local(columns: dict[str, Sequence[Any]], *, source: str | None = None,
          method: str = "pearson") -> Partial:
    names = sorted(columns)
    pairs: dict[str, dict[str, float]] = {}
    for a_i, a in enumerate(names):
        for b in names[a_i + 1:]:
            xa, xb = columns[a], columns[b]
            n = 0
            sx = sy = sxx = syy = sxy = 0.0
            for va, vb in zip(xa, xb):
                if va is None or vb is None:
                    continue           # pairwise complete
                x, y = float(va), float(vb)
                n += 1
                sx += x; sy += y
                sxx += x * x; syy += y * y; sxy += x * y
            pairs[f"{a}|{b}"] = {"n": n, "sx": sx, "sy": sy,
                                 "sxx": sxx, "syy": syy, "sxy": sxy}
    data: dict[str, Any] = {"method": method, "pairs": pairs,
                            "n_sources": 1}
    if method == "spearman":
        data["local_rho"] = {k: _spearman_local(columns, k) for k in pairs}
    return Partial(kind=KIND, source=source, data=data)


def _spearman_local(columns, key) -> dict[str, Any]:
    a, b = key.split("|")
    paired = [(x, y) for x, y in zip(columns[a], columns[b])
              if x is not None and y is not None]
    if len(paired) < 3:
        return {"n": len(paired), "rho": None}
    from scipy import stats as _st
    rho = _st.spearmanr([p[0] for p in paired], [p[1] for p in paired]).statistic
    return {"n": len(paired), "rho": float(rho)}


def merge(partials: Sequence[Partial]) -> Partial:
    parts = list(partials)
    method = parts[0].data["method"]
    pairs: dict[str, dict[str, float]] = {}
    for p in parts:
        for key, s in p.data["pairs"].items():
            acc = pairs.setdefault(key, {k: 0.0 for k in
                                         ("n", "sx", "sy", "sxx", "syy", "sxy")})
            for k, v in s.items():
                acc[k] += v
    data: dict[str, Any] = {"method": method, "pairs": pairs,
                            "n_sources": len(parts)}
    if method == "spearman":
        # Weighted mean of per-node coefficients. Not the pooled Spearman,
        # and finalize says so.
        rho: dict[str, Any] = {}
        for key in pairs:
            num = den = 0.0
            for p in parts:
                loc = p.data.get("local_rho", {}).get(key) or {}
                if loc.get("rho") is not None:
                    num += loc["rho"] * loc["n"]
                    den += loc["n"]
            rho[key] = {"rho": (num / den if den else None), "n": int(den)}
        data["local_rho"] = rho
    return Partial(kind=KIND, by_source=_merge_by_source(parts), data=data)


def _pearson(s: dict[str, float]) -> float | None:
    n = s["n"]
    if n < 3:
        return None
    num = n * s["sxy"] - s["sx"] * s["sy"]
    den = math.sqrt(max(n * s["sxx"] - s["sx"] ** 2, 0.0)) * \
        math.sqrt(max(n * s["syy"] - s["sy"] ** 2, 0.0))
    return num / den if den else None


def finalize(partial: Partial, policy: DisclosurePolicy) -> Result:
    d = partial.data
    method = d["method"]
    multi = d.get("n_sources", 1) > 1
    out: dict[str, Any] = {}
    for key, s in d["pairs"].items():
        n = int(s["n"])
        if method == "spearman":
            loc = d.get("local_rho", {}).get(key) or {}
            r = loc.get("rho")
        else:
            r = _pearson(s)
        out[key] = safe_correlation(n, r, policy)

    notes = ["A correlation describes how two measurements move together. "
             "It does not show that one causes the other."]
    exactness = EXACT
    if method == "spearman" and multi:
        exactness = APPROXIMATE
        notes.append(
            "Spearman needs the ranking of all the data at once. Across "
            "several sources this is a weighted average of each source's own "
            "coefficient, not the true pooled value.")
    elif method == "pearson":
        notes.append("Pearson is computed exactly across sources.")
    return Result(kind=KIND, pooled=out, by_source=partial.by_source,
                  exactness=exactness, notes=notes)
