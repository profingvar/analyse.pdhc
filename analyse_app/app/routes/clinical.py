"""Clinical patient-list API for the reformed analyse landing (#578).

Endpoints (care role or admin — see clinical_required):
  GET /api/cdrs                 → the CDRs the caller may select from
  GET /api/patients?cdr_ids=..  → org-scoped, CDR-enriched, spärr-aware list

The per-patient Dashboard data view and the admin spärr-log view are
separate follow-ups; this module delivers the "choose patient" list.
"""
from flask import Blueprint, current_app, g, jsonify, request

from app.analyse.federation import CdrRegistry
from app.analyse.patient_list import build_patient_list
from app.services.role_guards import clinical_required
from app.services.ips_client import IpsClient

bp = Blueprint("clinical_api", __name__, url_prefix="/api")


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
