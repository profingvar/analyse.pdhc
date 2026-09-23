"""Regression and survival, federated exactly (#659).

All three are exactly federatable, which is why they belong here rather than
in an "approximate" bucket:

- **Linear**: the node returns XᵀX, Xᵀy, yᵀy and n. Those add, and the
  normal equations are solved once at the coordinator. Exact.
- **Logistic**: the node returns the gradient and Hessian at the CURRENT
  coefficients each iteration. Those add too, so federated Newton-Raphson
  gives the same answer as pooling would — at the cost of one round trip per
  iteration.
- **Kaplan-Meier**: the node returns events and at-risk counts per interval.
  Those add. Exact.

What crosses the wire stays aggregate throughout. A Hessian is a matrix of
sums; it is not a dataset.

A note kept deliberately visible: a regression coefficient is still a
description. Nothing here licenses a causal reading, and the result notes say
so in the same words the rest of the engine uses.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from app.privacy.disclosure import DisclosurePolicy

from .base import EXACT, Partial, Result, _merge_by_source

LINEAR = "linear_regression"
LOGISTIC = "logistic_regression"
KM = "kaplan_meier"


# ── linear ────────────────────────────────────────────────────────────

def linear_local(rows: Sequence[Sequence[float]], y: Sequence[float], *,
                 source: str | None = None) -> Partial:
    """rows: one list of predictors per patient (intercept added here)."""
    p = (len(rows[0]) + 1) if rows else 1
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    yty = 0.0
    n = 0
    for xr, yv in zip(rows, y):
        if yv is None or any(v is None for v in xr):
            continue
        x = [1.0] + [float(v) for v in xr]
        n += 1
        yty += float(yv) ** 2
        for i in range(p):
            xty[i] += x[i] * float(yv)
            for j in range(p):
                xtx[i][j] += x[i] * x[j]
    return Partial(kind=LINEAR, source=source,
                   data={"n": n, "p": p, "xtx": xtx, "xty": xty, "yty": yty})


def linear_merge(partials: Sequence[Partial]) -> Partial:
    parts = list(partials)
    p = parts[0].data["p"]
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for part in parts:
        for i in range(p):
            xty[i] += part.data["xty"][i]
            for j in range(p):
                xtx[i][j] += part.data["xtx"][i][j]
    return Partial(kind=LINEAR, by_source=_merge_by_source(parts), data={
        "n": sum(x.data["n"] for x in parts), "p": p, "xtx": xtx, "xty": xty,
        "yty": sum(x.data["yty"] for x in parts)})


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting. Returns None for a singular
    system rather than a plausible-looking answer."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def linear_finalize(partial: Partial, policy: DisclosurePolicy,
                    names: Sequence[str] | None = None) -> Result:
    d = partial.data
    n, p = d["n"], d["p"]
    notes = ["A regression describes how the measurements move together. "
             "It does not show that one causes the other."]
    if n < max(policy.k_min, p + 2):
        return Result(kind=LINEAR, exactness=EXACT, notes=notes + [
            "Too few patients to fit this model safely."],
            pooled={"suppressed": True}, by_source=partial.by_source)

    beta = _solve(d["xtx"], d["xty"])
    if beta is None:
        return Result(kind=LINEAR, exactness=EXACT, pooled={"suppressed": True},
                      by_source=partial.by_source, notes=notes + [
                          "The predictors are too closely related to "
                          "separate their contributions."])
    rss = d["yty"] - sum(beta[i] * d["xty"][i] for i in range(p))
    dof = n - p
    sigma2 = max(rss / dof, 0.0) if dof > 0 else 0.0
    labels = ["(intercept)"] + list(names or [f"x{i}" for i in range(1, p)])
    return Result(kind=LINEAR, exactness=EXACT, by_source=partial.by_source,
                  notes=notes + ["Fitted exactly across sources."],
                  pooled={"n": n, "coefficients": dict(zip(labels, beta)),
                          "residual_sd": math.sqrt(sigma2),
                          "suppressed": False})


# ── Kaplan-Meier ──────────────────────────────────────────────────────

def km_local(intervals: Sequence[tuple[int, int, int]], *,
             source: str | None = None) -> Partial:
    """intervals: (period, events, at_risk) per period."""
    agg: dict[int, dict[str, int]] = {}
    for period, events, at_risk in intervals:
        slot = agg.setdefault(int(period), {"events": 0, "at_risk": 0})
        slot["events"] += int(events)
        slot["at_risk"] += int(at_risk)
    return Partial(kind=KM, source=source, data={"intervals": agg})


def km_merge(partials: Sequence[Partial]) -> Partial:
    out: dict[int, dict[str, int]] = {}
    for p in partials:
        for period, slot in p.data["intervals"].items():
            k = int(period)
            acc = out.setdefault(k, {"events": 0, "at_risk": 0})
            acc["events"] += slot["events"]
            acc["at_risk"] += slot["at_risk"]
    return Partial(kind=KM, by_source=_merge_by_source(list(partials)),
                   data={"intervals": out})


def km_finalize(partial: Partial, policy: DisclosurePolicy) -> Result:
    steps = []
    surv = 1.0
    hidden = 0
    for period in sorted(partial.data["intervals"], key=int):
        slot = partial.data["intervals"][period]
        at_risk, events = slot["at_risk"], slot["events"]
        if at_risk <= 0:
            continue
        surv *= (1.0 - events / at_risk)
        if at_risk < policy.k_min:
            hidden += 1
            steps.append({"period": period, "at_risk": "<k",
                          "events": "<k", "survival": None})
        else:
            steps.append({"period": period, "at_risk": at_risk,
                          "events": events, "survival": surv})
    notes = ["The curve shows the proportion still free of the event over "
             "time. It describes what happened, not why."]
    if hidden:
        notes.append(f"{hidden} periods are hidden because too few patients "
                     f"were still being followed.")
    return Result(kind=KM, exactness=EXACT, by_source=partial.by_source,
                  notes=notes, pooled={"steps": steps})
