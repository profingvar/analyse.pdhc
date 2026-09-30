"""The read purpose a caller must declare on a federated patient-data read.

#700. Both consent gates on the CDRs (#664 on cdr1-5, #699 on cdr_6) are
opt-in by design: a caller that declares nothing passes through untouched.
That was the right call — a gate that started refusing existing readers would
have taken the platform down — but it means a gate only fires when somebody
opts in, and on the federated endpoints nobody did.

So ``/api/v1/canonical/<table>`` and ``/api/v1/openehr/*`` fanned a named
patient's rows out of every CDR with analyse's service key, applied no
``analysis_filter`` locally, and sent no purpose. Neither side filtered.

## Why this fails closed rather than declaring a purpose on the caller's behalf

The correct basis for a read is a property of the CALLER, not of the endpoint.
These two endpoints have no caller in any repo — they are surface kept for
``gateway.pdhc`` and ``monitor.pdhc`` — so any purpose analyse picked would be
a guess, and the wrong guess is not neutral: declaring ``research`` applies an
EHDS opt-out to what might be a care-delivery read and silently removes rows
the caller is entitled to.

Requiring the declaration puts the choice where the knowledge is. A caller
that has not been updated gets a 400 naming the valid values, which is a loud,
diagnosable failure instead of a silent consent bypass.

## Why only the secondary-use values

cdr's ``DECLARABLE_SERVICE_PURPOSES`` is the authority and it admits only
``research``, ``statistics`` and ``quality_registry``. Its reasoning is worth
repeating: letting a service declare a primary-use value such as ``care``
"would turn this from a gate into a bypass". A care-delivery read is a
different path — cdr's clinical read, which takes ``X-Access-Purpose:
care-delivery`` and a break-glass reason — not this one. Mirrored here rather
than imported, because analyse must not depend on cdr's source tree; the test
asserts the two sets agree.

``/api/v1/stats`` is deliberately exempt. It returns row counts per table and
no patient rows at all, so there is nothing for a consent join to filter and a
purpose would be noise.
"""
from __future__ import annotations

from flask import jsonify, request

#: Mirrors cdr's DECLARABLE_SERVICE_PURPOSES. Keep in step; the CDR rejects
#: anything outside it anyway, so drifting only moves the 400 further away
#: from the caller.
DECLARABLE_SERVICE_PURPOSES = frozenset({
    "research", "statistics", "quality_registry",
})

PURPOSE_HEADER = "X-Access-Purpose"
RESEARCH_PROJECTS_HEADER = "X-Research-Project-Guids"


def purpose_headers() -> tuple[dict[str, str] | None, tuple | None]:
    """``(headers_to_forward, error_response)`` — exactly one is not None.

    The headers are forwarded verbatim to every CDR in the fan-out, so the
    CDR's own gate is what actually filters. Analyse validates first only so
    the error names analyse's endpoint rather than surfacing as five identical
    per-CDR failures.
    """
    raw = (request.headers.get(PURPOSE_HEADER) or "").strip().lower()
    if not raw:
        return None, (jsonify({
            "error": "purpose_required",
            "message": (
                f"{PURPOSE_HEADER} is required on this endpoint. Declare one "
                f"of {', '.join(sorted(DECLARABLE_SERVICE_PURPOSES))}. This "
                f"read crosses every CDR and the patient's analysis consent "
                f"is applied against the declared purpose."),
        }), 400)
    if raw not in DECLARABLE_SERVICE_PURPOSES:
        return None, (jsonify({
            "error": "purpose_not_declarable",
            "message": (
                f"{PURPOSE_HEADER}: a service may declare only "
                f"{', '.join(sorted(DECLARABLE_SERVICE_PURPOSES))}. A "
                f"care-delivery read uses cdr's clinical read, not this "
                f"endpoint."),
        }), 400)

    headers = {PURPOSE_HEADER: raw}
    if raw == "research":
        # cdr rejects research with no project guids, so catch it here where
        # the message can say which endpoint is missing them.
        projects = (request.headers.get(RESEARCH_PROJECTS_HEADER) or "").strip()
        if not projects:
            return None, (jsonify({
                "error": "research_projects_required",
                "message": (
                    f"{RESEARCH_PROJECTS_HEADER} is required when "
                    f"{PURPOSE_HEADER} is 'research': consent is granted per "
                    f"project, not to research in general."),
            }), 400)
        headers[RESEARCH_PROJECTS_HEADER] = projects
    return headers, None
