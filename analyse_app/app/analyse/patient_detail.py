"""Per-patient clinical detail across the selected CDRs (ticket #579).

The "choose patient" list (#578) links each row to ``/patient/<guid>``; this
module assembles the data behind that page — the patient's observations from
the selected CDR(s), grouped into per-concept series, with spärr enforcement
applied on the *data*, not merely flagged.

Spärr contract (v1, coarse — matches the analyse-not-deployed default and the
dashboard "safe coarse hide" fallback, memory sparr_zone_model):
  - non-admin caller + patient has any active block → clinical data is
    HIDDEN (empty series); only the metadata banner survives (PDL Ch 4 §4).
  - admin caller + active block → break-glass EXPOSURE: data is returned and
    ``exposure`` is set so the route logs a ``sparr_lift_exposure`` audit row.

One fanout per selected CDR (patient-filtered), grouped locally — O(#CDRs).
"""
from __future__ import annotations

import logging

from app.analyse.federation import fanout
from app.analyse.patient_list import _obs_date, _patient_of
from app.auth import scope_org_guids
from app.services import patient_directory as pdir

logger = logging.getLogger(__name__)


def _series_key(res: dict) -> tuple[str, str]:
    """(code, human label) for the concept this observation measures.

    Prod CDR observations carry ``code.coding[0]`` as the canonical concept
    coding — ``code`` is the plan.pdhc Concept guid (memory
    plan_pdhc_fhir_terminology_live), ``display`` the label. Falls back to
    ``code.text`` then a placeholder so a malformed row still groups."""
    coding = ((res.get("code") or {}).get("coding") or [])
    if coding:
        c0 = coding[0] or {}
        code = c0.get("code") or (res.get("code") or {}).get("text") or "?"
        return code, (c0.get("display") or code)
    text = (res.get("code") or {}).get("text") or "?"
    return text, text


def _value_of(res: dict):
    """Numeric value when present, else the string/coded value, else None."""
    vq = res.get("valueQuantity")
    if isinstance(vq, dict) and vq.get("value") is not None:
        try:
            return float(vq["value"])
        except (TypeError, ValueError):
            return vq.get("value")
    if res.get("valueString") is not None:
        return res.get("valueString")
    vcc = res.get("valueCodeableConcept")
    if isinstance(vcc, dict):
        codings = vcc.get("coding") or []
        if codings:
            return codings[0].get("display") or codings[0].get("code")
        return vcc.get("text")
    return None


def _unit_of(res: dict) -> str | None:
    vq = res.get("valueQuantity")
    if isinstance(vq, dict):
        return vq.get("unit") or vq.get("code")
    return None


def _group_series(results, patient_guid):
    """Group every CDR's patient-filtered observations into per-concept series.

    Returns ``(series, total_points)``. Each series is
    ``{code, label, unit, count, latest, points:[{at, value, cdr_id}]}``
    with points sorted oldest→newest; series sorted by size then label.
    """
    groups: dict[str, dict] = {}
    total = 0
    for r in results:
        if not getattr(r, "ok", False) or not isinstance(r.body, dict):
            continue
        cid = getattr(r, "cdr_id", None) or "?"
        for entry in r.body.get("entry") or []:
            res = entry.get("resource") or {}
            pg = _patient_of(res)
            # The fanout is patient-filtered server-side, but a permissive CDR
            # could echo extra rows; keep only this patient's.
            if patient_guid and pg and pg != patient_guid:
                continue
            code, label = _series_key(res)
            grp = groups.get(code)
            if grp is None:
                grp = groups[code] = {"code": code, "label": label,
                                      "unit": _unit_of(res), "points": []}
            if not grp["unit"]:
                grp["unit"] = _unit_of(res)
            at = _obs_date(res)
            grp["points"].append({"at": at, "value": _value_of(res),
                                  "cdr_id": cid})
            total += 1

    series = []
    for grp in groups.values():
        grp["points"].sort(key=lambda p: p["at"] or "")
        grp["count"] = len(grp["points"])
        grp["latest"] = grp["points"][-1]["at"] if grp["points"] else None
        series.append(grp)
    series.sort(key=lambda s: (-s["count"], s["label"] or ""))
    return series, total


def build_patient_detail(blob, patient_guid, cdr_ids, registry, *,
                         bearer=None, blocks=None):
    """Assemble one patient's spärr-aware clinical detail across the CDRs.

    ``blocks`` is the patient's active spärr blocks (list; truthy = blocked).
    The caller (route) fetches them once so it can also fail-closed for a
    non-admin when ips is unreachable.
    """
    is_admin = bool(blob.get("is_su_admin"))
    org_guids = scope_org_guids(blob)
    org_header = ",".join(str(g) for g in (org_guids or []))

    block_present = bool(blocks)
    hidden = block_present and not is_admin        # non-admin: hide the data
    exposure = block_present and is_admin          # admin break-glass: log it

    demo = pdir.get_patient(patient_guid, bearer=bearer) or {}

    series: list[dict] = []
    total = 0
    if hidden:
        fanout_mode = "hidden"
    else:
        resp = fanout(
            registry,
            method="GET",
            path="api/v1/fhir/Observation",
            cdr_ids=cdr_ids or None,
            params={"patient": patient_guid, "_count": "100000"},
            org_guids_header=org_header,
            is_admin_header=is_admin,
        )
        fanout_mode = resp.mode
        series, total = _group_series(resp.results, patient_guid)

    selected = list(cdr_ids) if cdr_ids else [e.cdr_id for e in registry.all]
    return {
        "patient_guid": patient_guid,
        "name": demo.get("name"),
        "birth_year": demo.get("birth_year"),
        "blocked": hidden,
        "block_present": block_present,
        "exposure": exposure,
        "is_admin": is_admin,
        "series": series,
        "total_points": total,
        "fanout_mode": fanout_mode,
        "cdr_ids": selected,
    }
