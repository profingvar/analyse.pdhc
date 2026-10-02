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


#: Starting points for the researcher landing (#688).
#:
#: Deliberately phrased as QUESTIONS, and deliberately defined HERE rather than
#: as a JavaScript literal in the template: every one of them must be a
#: predicate the resolver actually accepts, and that is only checkable if they
#: are data the tests can import. Offering a starting point that cannot run is
#: worse than offering none — it is the specific failure #688 set out to catch.
#:
#: Keys must stay within what ``CohortFilter.from_dict`` reads; the test
#: ``test_every_starter_is_a_predicate_the_resolver_accepts`` enforces that.
COHORT_STARTERS = [
    {"q": "Everyone, every source",
     "why": "The widest cohort. Start here to see the ceiling.",
     "filter": {"cdr_ids": [], "demographics": {}}},
    {"q": "Working-age adults",
     "why": "40–70, all sources.",
     "filter": {"cdr_ids": [], "demographics": {"age_min": 40, "age_max": 70}}},
    {"q": "Older adults",
     "why": "70 and above, all sources.",
     "filter": {"cdr_ids": [], "demographics": {"age_min": 70}}},
    {"q": "Women, all ages",
     "why": "A sex-stratified starting point.",
     "filter": {"cdr_ids": [], "demographics": {"sex": "female"}}},
    {"q": "One source only",
     "why": "Scope to a single CDR — take the id from the table above.",
     "filter": {"cdr_ids": ["cdr2"], "demographics": {}}},
]


@bp.get("/")
@audit_read
def landing():
    # #663: the cohort/group workspace is the landing view. It was briefly the
    # org-scoped patient list (#578); that belongs to dashboard.pdhc now.
    return render_template("researcher_workspace.html",
                           starters=COHORT_STARTERS)


@bp.get("/researcher")
@audit_read
def researcher_workspace():
    # Kept as a stable alias so existing bookmarks and links still resolve.
    return render_template("researcher_workspace.html",
                           starters=COHORT_STARTERS)
