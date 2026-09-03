"""ips.pdhc patient-directory client for the clinical patient list (#578).

Org-scoped patient lists come from ips.pdhc — the authoritative
patient↔clinic registry (PatientClinicAssignment). Demographics (name,
birth year) come from the same source. Kept separate from
``services.ips_client`` (blocks/consent) so those hot paths stay untouched.

ips reads only the ``Authorization`` header (Bearer or ``ApiKey <key>``);
``X-API-Key`` is ignored (see infra note ips_auth_header_scheme).
"""
import os
import logging

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = float(os.environ.get("IPS_TIMEOUT", "8.0"))


def _base() -> str:
    return (os.environ.get("IPS_BASE_URL", "") or "").rstrip("/")


def _headers(bearer: str | None = None) -> dict:
    h = {"Accept": "application/json"}
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    else:
        key = (os.environ.get("IPS_API_KEY")
               or os.environ.get("ANALYSE_PDHC_SERVICE_KEY") or "")
        if key:
            h["Authorization"] = f"ApiKey {key}"
    return h


def _birth_year(bd) -> str | None:
    if not bd:
        return None
    s = str(bd)
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else None


def _name_of(res: dict) -> str | None:
    """Accept a flat name, split family/given, or a FHIR HumanName list.

    ips ``/clinics/<g>/patients`` returns PatientIndex.to_dict() — a flat
    ``{family_name, given_name, ...}`` (verified #579/item-3), NOT ``name``.
    The FHIR ``/Patient/<g>`` endpoint returns ``name:[{family, given[]}]``.
    """
    name = res.get("name") or res.get("display_name") or res.get("full_name")
    if isinstance(name, list) and name:
        n0 = name[0] or {}
        given = " ".join(n0.get("given", []) or [])
        family = n0.get("family", "") or ""
        name = (f"{given} {family}").strip() or n0.get("text")
    if name:
        return name
    # Flat PatientIndex shape from ips clinics/<g>/patients.
    fam = res.get("family_name")
    giv = res.get("given_name")
    if fam or giv:
        return (f"{giv or ''} {fam or ''}").strip() or None
    return None


def list_clinic_patients(care_unit_guid: str, bearer: str | None = None) -> list[dict]:
    """GET /api/v1/clinics/<guid>/patients → [{patient_guid, name, birth_year}].

    Tolerant of a flat-JSON list or a FHIR searchset Bundle. Fails soft
    (returns []) so a single unreachable clinic never breaks the list.
    """
    base = _base()
    if not base or not care_unit_guid:
        return []
    url = f"{base}/api/v1/clinics/{care_unit_guid}/patients"
    try:
        r = requests.get(url, headers=_headers(bearer), timeout=_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("ips clinic-patients %s error: %s", care_unit_guid, e)
        return []
    if r.status_code != 200:
        logger.warning("ips clinic-patients %s -> %d", care_unit_guid, r.status_code)
        return []
    try:
        data = r.json()
    except ValueError:
        return []

    if isinstance(data, list):
        rows = data
    else:
        rows = data.get("patients") or data.get("entry") or []

    out = []
    for p in rows:
        res = p.get("resource") if isinstance(p, dict) and "resource" in p else p
        if not isinstance(res, dict):
            continue
        guid = (res.get("patient_guid") or res.get("guid") or res.get("id"))
        if not guid:
            continue
        bd = (res.get("birth_year") or res.get("birthDate")
              or res.get("birthdate") or res.get("birth_date"))
        out.append({
            "patient_guid": guid,
            "name": _name_of(res),
            "birth_year": _birth_year(bd),
        })
    return out


def get_patient(patient_guid: str, bearer: str | None = None) -> dict:
    """Best-effort demographics for one patient (admin path)."""
    base = _base()
    if not base or not patient_guid:
        return {}
    url = f"{base}/api/v1/fhir/Patient/{patient_guid}"
    try:
        r = requests.get(url, headers=_headers(bearer), timeout=_TIMEOUT)
    except requests.RequestException:
        return {}
    if r.status_code != 200:
        return {}
    try:
        res = r.json()
    except ValueError:
        return {}
    return {"name": _name_of(res), "birth_year": _birth_year(res.get("birthDate"))}
