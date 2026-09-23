"""Data completeness (#652).

The least glamorous analysis and often the most useful: before anyone asks
what the data says, someone should ask whether there is any.

Three views, all exact: observations per patient as a histogram, missingness
per variable, and rows by source and by author organisation. The last is
possible only since cdr #665 added ``author_org_guid`` — before that, "who
contributed this data" could only be answered as "who submitted it", which is
a different question.
"""
from __future__ import annotations

from typing import Any, Sequence

from app.privacy.disclosure import (
    DisclosurePolicy, merge_small_bins, suppress_counts,
)

from .base import EXACT, Partial, Result, _merge_by_source

KIND = "completeness"


def local(rows: Sequence[dict[str, Any]], variables: Sequence[str], *,
          source: str | None = None) -> Partial:
    per_patient: dict[str, int] = {}
    missing = {v: 0 for v in variables}
    present = {v: 0 for v in variables}
    by_author: dict[str, int] = {}

    for r in rows:
        pid = r.get("pid")
        if pid:
            per_patient[pid] = per_patient.get(pid, 0) + 1
        for v in variables:
            if r.get(v) is None:
                missing[v] += 1
            else:
                present[v] += 1
        author = r.get("meta.author_org") or "(not recorded)"
        by_author[author] = by_author.get(author, 0) + 1

    # The per-patient counts are turned into a DISTRIBUTION here, on the node.
    # Sending the per-patient numbers would be a per-patient dataset.
    dist: dict[str, int] = {}
    for count in per_patient.values():
        dist[str(count)] = dist.get(str(count), 0) + 1

    return Partial(kind=KIND, source=source, data={
        "obs_per_patient": dist, "missing": missing, "present": present,
        "by_author_org": by_author, "n_patients": len(per_patient),
        "n_rows": len(rows)})


def merge(partials: Sequence[Partial]) -> Partial:
    parts = list(partials)

    def _sum_dicts(key):
        out: dict[str, int] = {}
        for p in parts:
            for k, v in p.data[key].items():
                out[k] = out.get(k, 0) + v
        return out

    return Partial(kind=KIND, by_source=_merge_by_source(parts), data={
        "obs_per_patient": _sum_dicts("obs_per_patient"),
        "missing": _sum_dicts("missing"),
        "present": _sum_dicts("present"),
        "by_author_org": _sum_dicts("by_author_org"),
        "n_patients": sum(p.data["n_patients"] for p in parts),
        "n_rows": sum(p.data["n_rows"] for p in parts)})


def finalize(partial: Partial, policy: DisclosurePolicy) -> Result:
    d = partial.data
    dist = sorted(d["obs_per_patient"].items(), key=lambda kv: int(kv[0]))
    bins = merge_small_bins([(f"{k} observations", v) for k, v in dist], policy)

    rates = {}
    for var, miss in d["missing"].items():
        total = miss + d["present"].get(var, 0)
        rates[var] = {
            "missing": miss, "total": total,
            "percent_missing": (100.0 * miss / total) if total else None,
        }

    notes = ["Completeness describes how much data exists, not what it says."]
    if any(r["percent_missing"] and r["percent_missing"] > 50 for r in rates.values()):
        notes.append("More than half the values are missing for at least one "
                     "variable. Any summary of that variable describes the "
                     "patients who happened to be measured.")
    if "(not recorded)" in d["by_author_org"]:
        notes.append("Some rows do not record which organisation authored "
                     "them; that field is new and older rows predate it.")

    return Result(kind=KIND, exactness=EXACT, by_source=partial.by_source,
                  notes=notes, pooled={
                      "observations_per_patient":
                          [{"label": l, "patients": c} for l, c in bins],
                      "missingness": rates,
                      "rows_by_author_org":
                          suppress_counts(d["by_author_org"], policy),
                      "n_patients": d["n_patients"], "n_rows": d["n_rows"]})
