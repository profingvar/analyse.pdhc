"""Clinical patient-list API for the reformed analyse landing (#578).

Endpoints (care role or admin — see clinical_required):
  GET /api/cdrs                    → the CDRs the caller may select from
  GET /api/patients?cdr_ids=..     → org-scoped, CDR-enriched, spärr-aware list
  GET /api/patient/<guid>?cdr_ids= → one patient's spärr-enforced detail (#579)
  GET /api/admin/sparr-log         → admin spärr-exposure audit log (#579)
"""
from flask import Blueprint, current_app, g, jsonify, request

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
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip() or None
    return None


def _block_checker(bearer):
    """Return f(patient_guid)->bool using ips.pdhc active blocks (spärr)."""
    client = IpsClient(token=bearer)

    def check(patient_guid):
        try:
            return bool(client.fetch_active_blocks(patient_guid))
        except Exception:
            # Fail closed for spärr: if we cannot confirm, treat as blocked
            # so no data leaks past an unverifiable block.
            return True

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


def _active_blocks(bearer, patient_guid):
    """(blocks, fail_closed) — the patient's active spärr blocks.

    ``fail_closed`` is True when ips could not be consulted, so the route
    treats a non-admin caller as blocked (no data leaks past an
    unverifiable block). fetch_active_blocks already swallows network
    errors to [] internally; this guards the unexpected-exception path."""
    client = IpsClient(token=bearer)
    try:
        return list(client.fetch_active_blocks(patient_guid)), False
    except Exception:  # noqa: BLE001
        return [], True


def _block_ids(blocks):
    out = []
    for b in blocks or []:
        gid = getattr(b, "guid", None)
        if gid is not None:
            out.append(str(gid))
    return out


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

    blocks, fail_closed = _active_blocks(bearer, guid)
    is_admin = bool(blob.get("is_su_admin"))
    if fail_closed and not is_admin and not blocks:
        # ips unreachable → cannot confirm the patient is un-blocked; treat as
        # blocked for a non-admin caller (fail closed). A sentinel keeps
        # build_patient_detail's block-present branch without a real Block.
        blocks = [object()]

    result = build_patient_detail(
        blob, guid, cdr_ids, _registry(), bearer=bearer, blocks=blocks,
    )

    if result["exposure"]:
        g._audit_event_type = "sparr_lift_exposure"
        g._audit_payload_snapshot = {"break_glass": True,
                                     "blocked_patient": True,
                                     "block_guids": _block_ids(blocks)}
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
