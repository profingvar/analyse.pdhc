"""Group comparison (#652).

The node returns the describe partial PER GROUP; everything federated is then
a function of those sums, so Welch's t, the mean difference with its CI, and
the standardised mean difference are all exact.

Two things this module refuses to do, both deliberate:

- It never says "effect" or "causes". Two groups differing is a description
  of those groups, and the brief is explicit that comparisons are
  descriptive.
- It puts the EFFECT SIZE FIRST and the p-value after. A p-value answers
  "would this difference be surprising if there were none", which is not the
  question a clinician asked. The standardised mean difference is also what
  the Table 1 view in AN-13 shows non-experts, in preference to p-values.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from app.privacy.disclosure import DisclosurePolicy, safe_group_comparison

from .base import EXACT, Partial, Result, _merge_by_source
from . import describe

KIND = "compare_groups"


def local(groups: dict[str, Sequence[Any]], *, source: str | None = None,
          categorical: bool = False, k_min: int = 5) -> Partial:
    return Partial(kind=KIND, source=source, data={
        "categorical": categorical,
        "groups": {name: describe.local(vals, categorical=categorical,
                                        k_min=k_min).data
                   for name, vals in groups.items()},
    })


def merge(partials: Sequence[Partial]) -> Partial:
    parts = list(partials)
    names: list[str] = []
    for p in parts:
        for n in p.data["groups"]:
            if n not in names:
                names.append(n)
    merged: dict[str, Any] = {}
    for n in names:
        subs = [Partial(kind=describe.KIND, data=p.data["groups"][n])
                for p in parts if n in p.data["groups"]]
        merged[n] = describe.merge(subs).data
    return Partial(kind=KIND, by_source=_merge_by_source(parts), data={
        "categorical": parts[0].data["categorical"],
        "groups": merged, "n_sources": len(parts)})


def _welch(a: dict, b: dict) -> dict[str, Any] | None:
    na, nb = a["n"], b["n"]
    if na < 2 or nb < 2:
        return None
    ma, mb = a["sum"] / na, b["sum"] / nb
    va = max((a["sumsq"] - na * ma * ma) / (na - 1), 0.0)
    vb = max((b["sumsq"] - nb * mb * mb) / (nb - 1), 0.0)
    se = math.sqrt(va / na + vb / nb)
    diff = mb - ma
    out: dict[str, Any] = {"difference": diff}

    # Standardised mean difference first: it is the size of the difference in
    # units of spread, which is what makes two comparisons comparable.
    pooled_sd = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)) \
        if na + nb > 2 else 0.0
    out["smd"] = (diff / pooled_sd) if pooled_sd > 0 else None

    if se > 0:
        dof = (va / na + vb / nb) ** 2 / (
            (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
        from scipy import stats as _st
        crit = _st.t.ppf(0.975, dof)
        out["ci95"] = [diff - crit * se, diff + crit * se]
        t = diff / se
        out["t"] = t
        out["dof"] = dof
        out["p_value"] = float(2 * _st.t.sf(abs(t), dof))
    return out


def finalize(partial: Partial, policy: DisclosurePolicy) -> Result:
    d = partial.data
    ns = {name: g["n"] for name, g in d["groups"].items()}
    gate = safe_group_comparison(ns, policy)
    notes = ["Groups are described and compared. A difference between them "
             "does not show that one thing caused the other."]

    if gate["suppressed"]:
        notes.append("This comparison is not shown: at least one group has "
                     "too few patients to describe safely. Try widening the "
                     "criteria.")
        return Result(kind=KIND, pooled={"suppressed": True}, notes=notes,
                      by_source=partial.by_source, exactness=EXACT)

    summary = {name: describe.finalize(
        Partial(kind=describe.KIND, data=g), policy).pooled
        for name, g in d["groups"].items()}
    pairs: dict[str, Any] = {}
    names = list(d["groups"])
    if not d["categorical"]:
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                res = _welch(d["groups"][a], d["groups"][b])
                if res:
                    pairs[f"{a} vs {b}"] = res
        notes.append("Differences, their confidence intervals and the "
                     "standardised difference are exact across sources.")
    return Result(kind=KIND, exactness=EXACT, by_source=partial.by_source,
                  notes=notes,
                  pooled={"groups": summary, "comparisons": pairs,
                          "suppressed": False})
