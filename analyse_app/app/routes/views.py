"""Web shell for the analyse.pdhc group-analysis workspace.

A thin HTML page. All data is fetched client-side from the researcher/cohort
JSON API (``/api/cohort...``), which authenticates via the session cookie set
by the SSO callback. The ``/`` landing route is gated by the global
before_request loader (analysis-phase); unauthenticated browsers are bounced
to ``/auth/login``.

#663 / ADR-0001: analyse.pdhc is the GROUP analysis tool. Individual-patient
analysis belongs to dashboard.pdhc and has been removed from here — the
patient chooser, the per-patient view and the spärr-log viewer are gone, and
the landing route is the group workspace.
"""
from __future__ import annotations

from flask import Blueprint, render_template

from app.services.audit import audit_read


bp = Blueprint("views", __name__)


@bp.get("/")
@audit_read
def landing():
    # #663: the cohort/group workspace is the landing view. It was briefly the
    # org-scoped patient list (#578); that belongs to dashboard.pdhc now.
    return render_template("researcher_workspace.html")


@bp.get("/researcher")
@audit_read
def researcher_workspace():
    # Kept as a stable alias so existing bookmarks and links still resolve.
    return render_template("researcher_workspace.html")
