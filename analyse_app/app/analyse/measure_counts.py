"""Measurements per patient and concept, across the federation (2026-11-18).

Backs the researcher's cohort preview and its "minimum measures" filter.

Each CDR is asked once, with the counting mode of its own Observation search
(`_pdhc_counts=patient,code`, cdr.pdhc since 2026-11-18), so its org scope,
consent filter and read audit apply exactly as for a row read. A CDR that does
not know the mode ignores the parameter and returns an ordinary Bundle (cdr_6
today, or a CDR not yet upgraded); those rows are counted here instead and the
source is marked `counted_from_rows`, and `truncated` when the page was full,
so a partial count is never presented as the whole.
"""
from __future__ import annotations

import re
from collections import defaultdict

#: Page size for a CDR that can only return rows. cdr.pdhc's own cap.
ROW_PAGE = 30000

_PLAN_URN = re.compile(r"^urn:pdhc:concept/([0-9a-fA-F-]{36})$")


def canonical(system: str | None, code: str | None) -> str | None:
    """One spelling per code: `<system>/<code>`, without a doubled slash.
    cdr_6 writes `urn:pdhc:concept/` as the system, cdr1-3 `urn:pdhc:concept`."""
    if not code:
        return None
    if not system:
        return code
    return f"{system.rstrip('/')}/{code}"


def normalise(code_canonical: str | None) -> str | None:
    if not code_canonical:
        return None
    system, _, code = code_canonical.rpartition("/")
    return canonical(system, code) if system else code_canonical


def _patient_of(obs: dict) -> str | None:
    ref = (obs.get("subject") or {}).get("reference") or ""
    return ref.rsplit("/", 1)[-1] if ref else None


def collect(results) -> tuple[dict[str, dict[str, int]], list[dict]]:
    """Fan-out results -> ({patient: {canonical: n}}, per-source status).

    Counts from different CDRs for the same patient and concept are added:
    they are different measurements held in different places."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sources = []
    for r in results:
        src = {"cdr_id": r.cdr_id, "ok": bool(r.ok), "mode": None,
               "truncated": False, "measures": 0}
        sources.append(src)
        body = r.body if (r.ok and isinstance(r.body, dict)) else None
        if body is None:
            continue
        if body.get("resourceType") == "PdhcCounts":
            src["mode"] = "counted_by_cdr"
            for c in body.get("counts") or []:
                p, k = c.get("patient"), normalise(c.get("code"))
                n = int(c.get("n") or 0)
                if p and k and n:
                    counts[p][k] += n
                    src["measures"] += n
        else:
            src["mode"] = "counted_from_rows"
            entries = body.get("entry") or []
            src["truncated"] = len(entries) >= ROW_PAGE
            for e in entries:
                obs = e.get("resource") or {}
                if obs.get("resourceType") not in (None, "Observation"):
                    continue
                coding = ((obs.get("code") or {}).get("coding") or [{}])[0]
                p = _patient_of(obs)
                k = canonical(coding.get("system"), coding.get("code"))
                if p and k:
                    counts[p][k] += 1
                    src["measures"] += 1
    return {p: dict(v) for p, v in counts.items()}, sources


def keep(members, counts: dict, min_measures: int) -> set[str]:
    """Members with at least `min_measures` measurements in total.
    0 (the default) keeps everyone, including patients with none."""
    members = set(members)
    if min_measures <= 0:
        return members
    return {p for p in members
            if sum((counts.get(p) or {}).values()) >= min_measures}


def concept_entry(canon: str) -> dict:
    """The shape `concept_display.fill_displays` names."""
    m = _PLAN_URN.match(canon)
    if m:
        return {"canonical": canon, "binding": "plan_concept",
                "concept_guid": m.group(1).lower(), "display": None}
    system, _, code = canon.rpartition("/")
    name = system.rsplit("/", 1)[-1] if system else ""
    return {"canonical": canon, "binding": "external_code", "concept_guid": None,
            "display": f"{name} {code}".strip()}


def summarise(members, counts: dict, min_measures: int, *,
              max_rows: int = 5000) -> dict:
    """The preview. Every CANDIDATE is listed with its per-concept counts and
    a `kept` flag, so the page can move the minimum-measures slider without
    asking again; `concepts` and `n` describe the kept set at `min_measures`."""
    members = set(members)
    kept = keep(members, counts, min_measures)
    totals = {p: sum((counts.get(p) or {}).values()) for p in members}
    concepts: dict[str, dict] = {}
    for p in members:
        for k, n in (counts.get(p) or {}).items():
            c = concepts.setdefault(k, {**concept_entry(k), "measures": 0, "patients": 0})
            if p in kept:
                c["measures"] += n
                c["patients"] += 1
    rows = sorted(members, key=lambda p: (-totals[p], p))
    return {
        "n_candidates": len(members),
        "n": len(kept),
        "min_measures": max(0, min_measures),
        "max_measures": max(totals.values(), default=0),
        "concepts": sorted(concepts.values(), key=lambda c: -c["measures"]),
        "patients": [{"patient_guid": p, "total": totals[p], "kept": p in kept,
                      "by_concept": dict(counts.get(p) or {})}
                     for p in rows[:max_rows]],
        "patients_truncated": len(rows) > max_rows,
    }
