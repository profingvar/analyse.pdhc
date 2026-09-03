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

Spärr contract (v1, per producing-clinic — uses the verified org_guid):
  - For each distinct producing ``org_guid`` in the patient's observations, ask
    ips ``/blocks/check?source_clinic_id=<org>`` (relationship-free,
    un-redacted — the ``/blocks`` list would 403/redact and fail OPEN).
  - Non-admin: observations from a blocked producing clinic are dropped; the
    caller still sees un-blocked clinics' data.
  - Admin: break-glass — blocked-clinic rows are shown and the route logs a
    ``sparr_lift_exposure`` audit row naming the exposed org(s).
  - If ips cannot answer for an org (``block_check`` returns None) while the
    patient has some block, a non-admin's rows for that org are dropped
    (fail SAFE); admin still sees them but no exposure is logged (unconfirmed).

One fanout per selected CDR (patient-filtered), grouped locally — O(#CDRs);
one ips /blocks/check per distinct producing-org (memoised).
"""
from __future__ import annotations

import logging

from app.analyse.federation import fanout
from app.analyse.patient_list import _obs_date, _patient_of
from app.auth import scope_org_guids
from app.services import patient_directory as pdir

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


def _iter_patient_resources(results, patient_guid):
    """Yield ``(cid, resource, org_guid)`` for this patient across all CDRs."""
    for r in results:
        if not getattr(r, "ok", False) or not isinstance(r.body, dict):
            continue
        cid = getattr(r, "cdr_id", None) or "?"
        for entry in r.body.get("entry") or []:
            res = entry.get("resource") or {}
            pg = _patient_of(res)
            if patient_guid and pg and pg != patient_guid:
                continue
            yield cid, res, _org_of(res)


def build_patient_detail(blob, patient_guid, cdr_ids, registry, *,
                         bearer=None, block_check=None):
    """Assemble one patient's spärr-aware clinical detail across the CDRs.

    ``block_check(org_guid) -> True | False | None`` answers "is data authored
    by this clinic blocked for the patient?" (backed by ips
    ``/blocks/check``). ``None`` means ips could not answer. When omitted, no
    spärr filtering is applied (used only by callers that have none, e.g.
    tests of the plain grouping).
    """
    is_admin = bool(blob.get("is_su_admin"))
    org_guids = scope_org_guids(blob)
    org_header = ",".join(str(g) for g in (org_guids or []))

    demo = pdir.get_patient(patient_guid, bearer=bearer) or {}
    selected = list(cdr_ids) if cdr_ids else [e.cdr_id for e in registry.all]

    resp = fanout(
        registry,
        method="GET",
        path="api/v1/fhir/Observation",
        cdr_ids=cdr_ids or None,
        params={"patient": patient_guid, "_count": "100000"},
        org_guids_header=org_header,
        is_admin_header=is_admin,
    )
    rows = list(_iter_patient_resources(resp.results, patient_guid))

    # One /blocks/check per distinct producing-org (memoised in the callable).
    status: dict = {}
    if block_check is not None:
        for org in {org for _, _, org in rows if org}:
            status[org] = block_check(org)
    any_block = any(v is True for v in status.values())

    groups: dict[str, dict] = {}
    total = 0
    filtered = 0
    exposed: set[str] = set()
    for cid, res, org in rows:
        st = status.get(org) if org else None
        # Blocked if confirmed blocked, or unresolvable while a block exists
        # (unknown org, or ips could not answer for a real org) → fail SAFE.
        blocked = (st is True) or (st is None and (org is None) and any_block) \
            or (st is None and org is not None and org in status)
        if blocked:
            if not is_admin:
                filtered += 1
                continue                          # hide from a care caller
            if st is True and org is not None:
                exposed.add(org)                  # admin break-glass exposure
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

    return {
        "patient_guid": patient_guid,
        "name": demo.get("name"),
        "birth_year": demo.get("birth_year"),
        "is_admin": is_admin,
        "block_present": any_block,
        "cdr_ids": selected,
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
