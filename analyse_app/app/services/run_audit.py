"""Audit for a group analysis run (#654).

Two logs, deliberately:

  - the PLATFORM log, so the run is visible where every other read is;
  - a LOCAL log on each node, so an organisation whose rows sit in someone
    else's CDR can still see that its data was used.

The second is the one that is easy to skip and the one that matters most to
the organisations the brief says must remain responsible for their own rows.
A node writes its own row whether or not the coordinator's write succeeds.

Counts are written SUPPRESSED. An audit log is read by more people than a
result is, and a per-source patient count of 3 discloses as much sitting in
an audit row as it would in a table.
"""
from __future__ import annotations

from typing import Any

from app.models.audit import AnalyseAudit
from app.extensions import db
from app.privacy.disclosure import SUPPRESSED, DisclosurePolicy


def _suppressed(n: int, policy: DisclosurePolicy):
    return SUPPRESSED if 0 < n < policy.k_min else n


def record_run(*, user_guid: str | None, user_org_guids: list[str],
               spec, sources: dict[str, int], policy: DisclosurePolicy,
               session_id: str | None = None, response_status: int = 200,
               suppression_applied: bool = False,
               node_id: str | None = None,
               commit: bool = True) -> AnalyseAudit:
    """Write one audit row for a run. ``sources`` is source id -> patients."""
    from app.spec import spec_hash

    row = AnalyseAudit(
        user_guid=user_guid,
        user_org_guids=list(user_org_guids or []),
        route=f"analysis:{'node' if node_id else 'coordinator'}",
        patient_guid=None,                 # a group run has no single patient
        n_rows_returned=None,
        response_status=response_status,
        session_id=session_id,
        event_type="analysis_run",
        spec_hash=spec_hash(spec),
        payload_snapshot={
            "purpose": spec.purpose.value,
            "linkage": spec.linkage.value,
            "title": spec.title,
            "analyses": [a.type for a in spec.analyses],
            "sources": sorted(sources),
            # Per source, suppressed. An audit log is read by more people
            # than a result is.
            "patients_per_source": {
                s: _suppressed(n, policy) for s, n in sorted(sources.items())},
            "k_min_applied": policy.k_min,
            "suppression_applied": bool(suppression_applied),
            "node_id": node_id,
        },
    )
    db.session.add(row)
    if commit:
        db.session.commit()
    return row


def runs_for_spec(spec_hash_value: str) -> list[dict[str, Any]]:
    """Every run of one spec — the question an auditor actually asks."""
    return [r.to_dict() for r in AnalyseAudit.query
            .filter_by(spec_hash=spec_hash_value)
            .order_by(AnalyseAudit.timestamp.desc()).all()]
