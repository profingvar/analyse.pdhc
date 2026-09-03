"""Web shell for the analyse.pdhc researcher/cohort workspace.

A thin HTML page. All data is fetched client-side from the ported researcher
JSON API (``/api/cohort...``), which authenticates via the session cookie set
by the SSO callback. The ``/`` landing route is gated by the global
before_request loader (analysis-phase); unauthenticated browsers are bounced
to ``/auth/login``.
"""
from __future__ import annotations

from flask import Blueprint, render_template

from app.services.audit import audit_read
from app.services.role_guards import admin_required


bp = Blueprint("views", __name__)


@bp.get("/")
@audit_read
def landing():
    # #578: the org-scoped patient list ("choose patient") is now the
    # landing view; the cohort/research workspace lives behind /researcher.
    return render_template("choose_patient.html")


@bp.get("/researcher")
@audit_read
def researcher_workspace():
    return render_template("researcher_workspace.html")


@bp.get("/patient/<guid>")
def patient_page(guid):
    # #579: per-patient Dashboard shell. The audited data touch is the
    # /api/patient/<guid> fetch this page makes client-side, so the HTML
    # shell itself is intentionally NOT @audit_read (no double logging).
    return render_template("patient_detail.html", patient_guid=guid)


@bp.get("/admin/sparr-log")
@admin_required
def sparr_log_page():
    # #579: admin oversight view over spärr exposures / hides.
    return render_template("sparr_log.html")
