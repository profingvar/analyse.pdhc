"""Org-scoped clinical patient list across the selected CDRs (ticket #578).

The reformed analyse landing ("choose patient") shows the patients a
clinician may see — those at the caller's own care unit(s) (affiliation
care_unit_guid, Rule 24 / zone-1), or ALL patients for an admin — enriched
per patient with the number of data points and the date of the most recent
observation, aggregated across the CDR(s) the caller selected.

Efficiency: one fanout per selected CDR for all Observations, grouped by
patient locally — O(#CDRs) calls, not O(#patients × #CDRs).
"""
import logging
from collections import defaultdict

from app.analyse.federation import fanout
from app.auth import scope_org_guids
from app.services import patient_directory as pdir

logger = logging.getLogger(__name__)


def _patient_of(res: dict) -> str | None:
    ref = (res.get("subject") or {}).get("reference", "") or ""
    return ref.rsplit("/", 1)[-1] if "/" in ref else (res.get("patient") or None)


def _obs_date(res: dict) -> str | None:
    return (res.get("effectiveDateTime")
            or (res.get("effectivePeriod") or {}).get("start")
            or res.get("issued"))


def cdr_stats(registry, cdr_ids, org_guids_header, is_admin):
    """One fanout per CDR for all Observations; group per patient.

    Returns (fanout_response, {patient_guid: {datapoints, latest, per_cdr}}).
    """
    resp = fanout(
        registry,
        method="GET",
        path="api/v1/fhir/Observation",
        cdr_ids=cdr_ids or None,
        params={"_count": "100000"},
        org_guids_header=org_guids_header,
        is_admin_header=is_admin,
    )
    agg = defaultdict(lambda: {"datapoints": 0, "latest": None,
                               "per_cdr": defaultdict(int)})
    for r in resp.results:
        if not getattr(r, "ok", False) or not isinstance(r.body, dict):
            continue
        cid = getattr(r, "cdr_id", None) or "?"
        for entry in r.body.get("entry") or []:
            res = entry.get("resource") or {}
            pg = _patient_of(res)
            if not pg:
                continue
            a = agg[pg]
            a["datapoints"] += 1
            a["per_cdr"][cid] += 1
            d = _obs_date(res)
            if d and (a["latest"] is None or d > a["latest"]):
                a["latest"] = d
    stats = {pg: {"datapoints": v["datapoints"], "latest": v["latest"],
                  "per_cdr": dict(v["per_cdr"])} for pg, v in agg.items()}
    return resp, stats


def build_patient_list(blob, cdr_ids, registry, *, bearer=None, block_checker=None):
    """Assemble the org-scoped, CDR-enriched, spärr-aware patient list.

    ``block_checker(patient_guid) -> bool`` returns True if the patient is
    spärrad from this (non-admin) caller. Admins bypass the block filter
    (their reads are break-glass and logged elsewhere).
    """
    is_admin = bool(blob.get("is_su_admin"))
    org_guids = scope_org_guids(blob)
    org_header = ",".join(str(g) for g in (org_guids or []))

    resp, stats = cdr_stats(registry, cdr_ids, org_header, is_admin)

    # Patient set + demographics.
    demo: dict[str, dict] = {}
    if is_admin:
        # Admin sees all patients that have data in the selected CDRs.
        guids = set(stats.keys())
        for g in guids:
            demo[g] = pdir.get_patient(g, bearer=bearer)
    else:
        # Care user: patients assigned to the caller's own care unit(s).
        for cu in org_guids:
            for p in pdir.list_clinic_patients(cu, bearer=bearer):
                demo[p["patient_guid"]] = {"name": p.get("name"),
                                           "birth_year": p.get("birth_year")}
        guids = set(demo.keys())

    rows = []
    n_blocked = 0
    for g in sorted(guids):
        s = stats.get(g, {"datapoints": 0, "latest": None, "per_cdr": {}})
        blocked = False
        if block_checker is not None and not is_admin:
            try:
                blocked = bool(block_checker(g))
            except Exception:
                blocked = False
        if blocked:
            n_blocked += 1
        d = demo.get(g, {})
        rows.append({
            "patient_guid": g,
            "name": d.get("name"),
            "birth_year": d.get("birth_year"),
            # Spärr hides the clinical counters for a non-admin caller;
            # the patient still appears (marked spärrad) so it is auditable.
            "datapoints": 0 if blocked else s["datapoints"],
            "latest_date": None if blocked else s["latest"],
            "per_cdr": {} if blocked else s["per_cdr"],
            "blocked": blocked,
        })

    selected = list(cdr_ids) if cdr_ids else [e.cdr_id for e in registry.all]
    return {
        "patients": rows,
        "count": len(rows),
        "blocked_count": n_blocked,
        "fanout_mode": resp.mode,
        "is_admin": is_admin,
        "cdr_ids": selected,
    }
