"""Health probe — canonical §10 shape in T7_sidewinder/CLAUDE.md.

Exposed at both ``/healthz`` (canonical; the CDR federation registry's
``discover()`` also pings CDRs at ``/healthz``) and ``/api/v1/health`` (the
name in the CLAUDE.md §3 table). Both are public (no auth) so the reverse
proxy / services.html can probe them, and both return HTTP 503 on a degraded
DB per §10. CORS is opened to www.pdhc.se so services.html can read the JSON.
"""
import logging

from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db
from app.version import VERSION


log = logging.getLogger(__name__)
bp = Blueprint("health", __name__)


def _health_payload():
    db_status = "connected"
    overall = 200
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        log.warning("healthz db probe failed: %s", e)
        db_status = "unavailable"
        overall = 503
    resp = jsonify({
        "status": "ok" if overall == 200 else "degraded",
        "database": db_status,
        "service": "analyse.pdhc",
        "version": VERSION,
    })
    resp.headers["Access-Control-Allow-Origin"] = "https://www.pdhc.se"
    resp.headers["Access-Control-Allow-Methods"] = "GET"
    resp.headers["Vary"] = "Origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp, overall


@bp.get("/healthz")
def healthz():
    return _health_payload()


@bp.get("/api/v1/health")
def api_v1_health():
    return _health_payload()
