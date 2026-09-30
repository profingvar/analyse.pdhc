"""Long observation rows → one wide record per patient (#715).

The engines were always written for wide, per-patient records. `completeness`
takes `rows` and reads `r.get(variable_name)`, counting `None` as missing;
`correlation` takes `columns: dict[name, values]`; `compare_groups` takes
`groups: dict[name, values]`. What a node reads from its CDR is the opposite
shape — one row per observation, with `concept` naming the variable and
`value` holding the number, because as `privacy/projection.py` puts it, "the
concept selects which rows, it is not a field of its own".

Nothing bridged the two. `node/runner.py::_dispatch` handed long rows straight
to the engines, so `describe(vars=["a"])` averaged every observation of every
concept, and `correlation` read keys that never existed and computed over
nothing. Measured before this module existed: describe returned n=20 on a
dataset where the named variable had 10 rows, and correlation returned n=0
with a well-formed Partial the coordinator would happily finalize.

The collapse rules are not invented here. The spec already fixes them:
`Agg`'s own docstring is "how repeated observations collapse to one value per
patient", `agg` is mandatory for an observation series and forbidden for
`demographics.*` / `meta.*`, and `completeness` defines a patient with no
qualifying observation as *present with a null* rather than absent — which is
the only way absence can be measured at all.
"""
from __future__ import annotations

from typing import Any, Sequence

#: Fields carried onto every record regardless of the declared variables,
#: because an engine reads them structurally rather than as a variable.
_CARRIED = ("source", "meta.author_org")

_FLAT_PREFIXES = ("demographics.", "meta.")


def _numeric(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _in_window(row: dict, window: tuple[int, int] | None) -> bool:
    """Both ends INCLUSIVE. "days 0-30" is how a clinician writes a window,
    and the spec validator only requires start <= end, so the choice is ours
    and this is the one that matches the words."""
    if window is None:
        return True
    day = row.get("day_offset")
    if day is None:
        return False
    return window[0] <= day <= window[1]


def _ordered(rows: list[dict]) -> list[dict]:
    """By day_offset, with undated rows last so they never masquerade as the
    earliest — `first` and `time_to_first_event` both depend on this."""
    return sorted(rows, key=lambda r: (r.get("day_offset") is None,
                                       r.get("day_offset") or 0))


def _slope(rows: list[dict]) -> float | None:
    pts = [(r["day_offset"], _numeric(r.get("value"))) for r in rows
           if r.get("day_offset") is not None]
    pts = [(float(d), v) for d, v in pts if v is not None]
    if len(pts) < 2:
        return None                    # undefined, therefore missing
    n = len(pts)
    sx = sum(d for d, _ in pts); sy = sum(v for _, v in pts)
    sxx = sum(d * d for d, _ in pts); sxy = sum(d * v for d, v in pts)
    denom = n * sxx - sx * sx
    if denom == 0:                     # every observation on the same day
        return None
    return (n * sxy - sx * sy) / denom


def _aggregate(agg: str, rows: list[dict]) -> Any:
    """One value per patient. Absence is None, never a fabricated zero — with
    one deliberate exception, `count`, where nothing really is zero."""
    if agg == "count":
        return len(rows)
    if not rows:
        return None
    ordered = _ordered(rows)
    if agg == "first":
        return ordered[0].get("value")
    if agg == "last":
        return ordered[-1].get("value")
    if agg == "time_to_first_event":
        # No event is not day zero. A patient who never had the event is
        # missing, and treating that as 0 would put them at the far left of
        # every survival curve.
        return ordered[0].get("day_offset")
    if agg == "slope":
        return _slope(rows)
    vals = [v for v in (_numeric(r.get("value")) for r in rows) if v is not None]
    if not vals:
        return None
    if agg == "mean":
        return sum(vals) / len(vals)
    if agg == "min":
        return min(vals)
    if agg == "max":
        return max(vals)
    raise ValueError(f"unknown aggregation '{agg}'")


def build(rows: Sequence[dict], spec) -> list[dict]:
    """One record per patient: {pid, source, <var name>: value|None, ...}.

    Every declared variable appears on every record. A patient with no
    qualifying observation gets None for it, which is exactly what
    `completeness` counts as missing.
    """
    by_pid: dict[str, list[dict]] = {}
    for r in rows:
        pid = r.get("pid")
        if pid:
            by_pid.setdefault(pid, []).append(r)

    frame: list[dict] = []
    for pid, prows in by_pid.items():
        rec: dict[str, Any] = {"pid": pid}
        for key in _CARRIED:
            val = next((r.get(key) for r in prows if r.get(key) is not None), None)
            if val is not None:
                rec[key] = val
        for var in spec.variables:
            src = var.from_
            if src.startswith(_FLAT_PREFIXES):
                # One value per patient by nature; the spec forbids agg here.
                rec[var.name] = next(
                    (r.get(src) for r in prows if r.get(src) is not None), None)
                continue
            selected = [r for r in prows
                        if r.get("concept") == src
                        and _in_window(r, var.window_days)]
            rec[var.name] = _aggregate(
                var.agg.value if hasattr(var.agg, "value") else var.agg,
                selected)
        frame.append(rec)
    return frame


def series(rows: Sequence[dict], var) -> list[tuple[int, float]]:
    """(day_offset, value) for one variable — the ONE engine input that is not
    per-patient. `over_time` plots observations against time, so collapsing to
    one value per patient first would destroy the thing being drawn."""
    out = []
    for r in rows:
        if r.get("concept") != var.from_ or not _in_window(r, var.window_days):
            continue
        day, val = r.get("day_offset"), _numeric(r.get("value"))
        if day is not None and val is not None:
            out.append((int(day), val))
    return out
