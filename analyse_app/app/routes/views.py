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


bp = Blueprint("views", __name__)


@bp.get("/")
@audit_read
def landing():
    return render_template("researcher_workspace.html")
