"""Clinical patient-list API for the reformed analyse landing (#578).

Endpoints (care role or admin — see clinical_required):
  GET /api/cdrs                    → the CDRs the caller may select from
  GET /api/patients?cdr_ids=..     → org-scoped, CDR-enriched, spärr-aware list
  GET /api/patient/<guid>?cdr_ids= → one patient's spärr-enforced detail (#579)
  GET /api/admin/sparr-log         → admin spärr-exposure audit log (#579)
"""
from flask import Blueprint, current_app, g, jsonify, request, session

from app.analyse.federation import CdrRegistry
from app.analyse.patient_list import build_patient_list
from app.analyse.patient_detail import build_patient_detail
from app.models import AnalyseAudit
from app.services.audit import audit_read
from app.services.role_guards import admin_required, clinical_required
from app.services.ips_client import IpsClient

bp = Blueprint("clinical_api", __name__, url_prefix="/api")

# Audit event_types the admin spärr-log surfaces: break-glass exposures of
# blocked data, non-admin hides, and the generic admin override.
_SPARR_EVENTS = ("sparr_lift_exposure", "sparr_hidden", "admin_override")


def _registry() -> CdrRegistry:
    if not hasattr(current_app, "_cdr_registry"):
        current_app._cdr_registry = CdrRegistry.from_config(current_app.config)
    return current_app._cdr_registry


def _blob() -> dict:
    b = getattr(g, "access_blob", None)
    return b if isinstance(b, dict) else {}


def _bearer() -> str | None:
    """The caller's SSO token, to forward to ips.pdhc.

    Browser calls authenticate via the Flask session cookie (the token is
    ``session["sso_token"]``, set by the SSO callback) — NOT an Authorization
    header. ips.pdhc rejects analyse's service key (verified #579/item-3), so
    ips calls MUST carry the user token. Header Bearer is kept as a fallback
    for service/test callers that do send one."""
    try:
        tok = session.get("sso_token")
    except RuntimeError:  # no request/session context
        tok = None
    if tok:
        return tok
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip() or None
    return None


def _block_checker(bearer):
    """f(patient_guid)->bool for the LIST spärr badge.

    Uses ips ``/blocks/metadata`` (relationship-free, counts only) — NOT the
    ``/blocks`` list, which 403s / redacts for unrelated callers and would
    make every patient look un-blocked (#579/item-3). Fail-safe on an ips
    error: show the badge (hide counters) rather than imply "no spärr"."""
    client = IpsClient(token=bearer)

    def check(patient_guid):
        has = client.patient_has_block(patient_guid)
        return True if has is None else bool(has)   # None (ips error) → safe

    return check


@bp.get("/cdrs")
@clinical_required
def list_cdrs():
    reg = _registry()
    return jsonify({"cdrs": [{"cdr_id": e.cdr_id} for e in reg.all]})


@bp.get("/patients")
@clinical_required
def patients():
    blob = _blob()
    raw = request.args.get("cdr_ids", "") or ""
    cdr_ids = [c.strip() for c in raw.split(",") if c.strip()] or None
    bearer = _bearer()
    result = build_patient_list(
        blob, cdr_ids, _registry(),
        bearer=bearer,
        block_checker=_block_checker(bearer),
    )
    return jsonify(result)


def _source_block_check(bearer, patient_guid):
    """Memoised f(org_guid)->True|False|None for one patient.

    Backed by ips ``/blocks/check?source_clinic_id=<org>`` — relationship-free
    and un-redacted. None = ips could not answer (caller fails safe)."""
    client = IpsClient(token=bearer)
    memo: dict = {}

    def check(org_guid):
        if not org_guid:
            return None
        if org_guid not in memo:
            memo[org_guid] = client.check_source_blocked(patient_guid, org_guid)
        return memo[org_guid]

    return check


@bp.get("/patient/<guid>")
@audit_read
@clinical_required
def patient_detail(guid):
    """One patient's spärr-enforced clinical detail across the CDRs.

    audit_read is the OUTER decorator so a 403 from clinical_required is
    still written to the kontroller log. The view annotates the audit row
    with the spärr disposition (exposure / hidden) before returning."""
    blob = _blob()
    raw = request.args.get("cdr_ids", "") or ""
    cdr_ids = [c.strip() for c in raw.split(",") if c.strip()] or None
    bearer = _bearer()

    result = build_patient_detail(
        blob, guid, cdr_ids, _registry(),
        bearer=bearer, block_check=_source_block_check(bearer, guid),
    )

    if result["exposure"]:
        g._audit_event_type = "sparr_lift_exposure"
        g._audit_payload_snapshot = {"break_glass": True,
                                     "exposed_orgs": result.get("exposed_orgs")}
    elif result["blocked"]:
        g._audit_event_type = "sparr_hidden"
    return jsonify(result)


@bp.get("/admin/sparr-log")
@admin_required
def sparr_log():
    """Recent spärr-exposure / hide audit rows (admin oversight, #579)."""
    try:
        limit = min(max(int(request.args.get("limit", 200)), 1), 1000)
    except (TypeError, ValueError):
        limit = 200
    rows = (AnalyseAudit.query
            .filter(AnalyseAudit.event_type.in_(_SPARR_EVENTS))
            .order_by(AnalyseAudit.timestamp.desc())
            .limit(limit).all())
    return jsonify({"events": [r.to_dict() for r in rows], "count": len(rows)})
