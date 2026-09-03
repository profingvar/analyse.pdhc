"""Per-patient clinical detail across the selected CDRs (ticket #579).

The "choose patient" list (#578) links each row to ``/patient/<guid>``; this
module assembles the data behind that page — the patient's observations from
the selected CDR(s), grouped into per-concept series, with spärr enforced on
the *data*, not merely flagged.

Live shapes verified #579/item-3 against cdr2–5 + ips.pdhc:
  - Observation ``code.coding`` carries TWO codings: ``[0]`` the LOINC/termbank
    standard code, ``[1]`` the plan.pdhc Concept guid (system ``…/Concept``).
    The Concept guid is the canonical, always-present identity (LOINC is absent
    for unmapped concepts) → series are keyed by it (Rule 18), labelled by the
    standard coding's display when present.
  - The owning clinic is in ``meta.security`` where ``code == "org_guid"`` →
    ``display`` (same shape ``researcher._extract_org`` reads).

Spärr contract (v1, per-clinic — uses the verified org_guid):
  - A clinic-scope block hides that clinic's observations. Non-admin: those
    rows are dropped; the caller still sees un-blocked clinics' data.
  - Admin: break-glass — blocked-clinic rows are shown and the route logs a
    ``sparr_lift_exposure`` audit row naming the exposed org(s).
  - Block fetch fails OPEN with a banner (platform legal model, ips_client
    docstring): org-scoping already constrains a non-admin's data.

One fanout per selected CDR (patient-filtered), grouped locally — O(#CDRs).
"""
from __future__ import annotations

import logging

from app.analyse.federation import fanout
from app.analyse.patient_list import _obs_date, _patient_of
from app.auth import scope_org_guids
from app.services import patient_directory as pdir
from app.services.ips_client import blocked_clinic_ids, has_any_active_block

logger = logging.getLogger(__name__)


def _codings(res: dict) -> list:
    return ((res.get("code") or {}).get("coding") or [])


def _concept_and_label(res: dict) -> tuple[str, str]:
    """(series key, human label) for the concept this observation measures.

    Key = the plan.pdhc Concept guid (coding whose system names ``Concept``);
    it is always present and is the canonical identity. Label prefers the
    standard (LOINC/termbank) coding's display, falling back to the concept
    display, the code text, then the key itself."""
    codings = _codings(res)
    concept = None
    standard = None
    for c in codings:
        sysu = (c.get("system") or "")
        if "plan.pdhc" in sysu and "concept" in sysu.lower():
            concept = c
        elif c.get("code") and standard is None:
            standard = c
    key_coding = concept or standard or (codings[0] if codings else {})
    code = key_coding.get("code") or (res.get("code") or {}).get("text") or "?"
    label = ((standard or {}).get("display")
             or (concept or {}).get("display")
             or (res.get("code") or {}).get("text")
             or code)
    return code, label


def _org_of(res: dict) -> str | None:
    """Owning clinic guid from ``meta.security[code==org_guid].display``."""
    for s in ((res.get("meta") or {}).get("security") or []):
        if s.get("code") == "org_guid":
            return s.get("display")
    return None


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


def _group_series(results, patient_guid, blocked_clinics, is_admin):
    """Group every CDR's patient-filtered observations into per-concept series,
    applying the per-clinic spärr filter.

    Returns ``(series, total_kept, filtered_count, exposed_orgs)``:
      - non-admin: observations from a blocked clinic are dropped
        (``filtered_count``); an observation with no resolvable org while a
        clinic block is active is also dropped (fail-safe — cannot prove it is
        not from the blocked clinic).
      - admin: all observations are kept; blocked-clinic orgs are collected in
        ``exposed_orgs`` for the break-glass audit row.
    """
    groups: dict[str, dict] = {}
    total = 0
    filtered = 0
    exposed: set[str] = set()
    for r in results:
        if not getattr(r, "ok", False) or not isinstance(r.body, dict):
            continue
        cid = getattr(r, "cdr_id", None) or "?"
        for entry in r.body.get("entry") or []:
            res = entry.get("resource") or {}
            pg = _patient_of(res)
            if patient_guid and pg and pg != patient_guid:
                continue
            org = _org_of(res)
            if blocked_clinics and (org in blocked_clinics or org is None):
                if not is_admin:
                    filtered += 1
                    continue                      # hide from a care caller
                if org is not None:
                    exposed.add(org)              # admin break-glass exposure
            code, label = _concept_and_label(res)
            grp = groups.get(code)
            if grp is None:
                grp = groups[code] = {"code": code, "label": label,
                                      "unit": _unit_of(res), "points": []}
            if not grp["unit"]:
                grp["unit"] = _unit_of(res)
            grp["points"].append({"at": _obs_date(res), "value": _value_of(res),
                                  "cdr_id": cid})
            total += 1

    series = []
    for grp in groups.values():
        grp["points"].sort(key=lambda p: p["at"] or "")
        grp["count"] = len(grp["points"])
        grp["latest"] = grp["points"][-1]["at"] if grp["points"] else None
        series.append(grp)
    series.sort(key=lambda s: (-s["count"], s["label"] or ""))
    return series, total, filtered, exposed


def build_patient_detail(blob, patient_guid, cdr_ids, registry, *,
                         bearer=None, blocks=None, ips_unavailable=False):
    """Assemble one patient's spärr-aware clinical detail across the CDRs.

    ``blocks`` is the patient's active spärr blocks (list of ips_client.Block).
    ``ips_unavailable`` is set by the route when the block fetch could not be
    made at all — a non-admin caller is then shown nothing (fail closed), which
    is stricter than the fail-open block path and reserved for a hard ips
    outage.
    """
    is_admin = bool(blob.get("is_su_admin"))
    org_guids = scope_org_guids(blob)
    org_header = ",".join(str(g) for g in (org_guids or []))

    blocks = list(blocks or [])
    blocked_clinics = blocked_clinic_ids(blocks)   # clinic-scope active blocks
    block_present = has_any_active_block(blocks)

    demo = pdir.get_patient(patient_guid, bearer=bearer) or {}
    selected = list(cdr_ids) if cdr_ids else [e.cdr_id for e in registry.all]
    base = {
        "patient_guid": patient_guid,
        "name": demo.get("name"),
        "birth_year": demo.get("birth_year"),
        "is_admin": is_admin,
        "block_present": block_present,
        "cdr_ids": selected,
    }

    if ips_unavailable and not is_admin:
        # Hard ips outage → cannot evaluate spärr → show nothing to a care user.
        return {**base, "series": [], "total_points": 0, "filtered_count": 0,
                "blocked": True, "exposure": False, "exposed_orgs": [],
                "fanout_mode": "unavailable", "block_present": True}

    resp = fanout(
        registry,
        method="GET",
        path="api/v1/fhir/Observation",
        cdr_ids=cdr_ids or None,
        params={"patient": patient_guid, "_count": "100000"},
        org_guids_header=org_header,
        is_admin_header=is_admin,
    )
    series, total, filtered, exposed = _group_series(
        resp.results, patient_guid, blocked_clinics, is_admin)

    return {
        **base,
        "series": series,
        "total_points": total,
        "filtered_count": filtered,
        # non-admin: some of THIS patient's data was hidden by an active block.
        "blocked": (not is_admin) and filtered > 0,
        # admin: blocked-clinic data was exposed under break-glass → log it.
        "exposure": is_admin and bool(exposed),
        "exposed_orgs": sorted(exposed),
        "fanout_mode": resp.mode,
    }
