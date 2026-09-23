"""analyse_audit — one row per analysis-phase read via analyse.pdhc.

PDL Ch 4 §3 kontroller log + Lag (2022:913) chain-of-custody. Ported from
dashboard.pdhc's ``DashboardAudit`` column-for-column (only the table/model
name changes) so downstream audit tooling (admin views, X1 tuple extraction)
treats the two identically across the dashboard/cd-assist/analyse split.
Written by the ``@audit_read`` decorator in ``services/audit.py``, and by
the researcher CSV export receipt (route='research_export').

Columns:
  - ``user_guid``       SSO ``user_guid`` of the caller, or a synthetic
    ``00000000-...-service-<svc>`` for service-key callers.
  - ``user_org_guids``  snapshot of the caller's care-unit scope at read time.
  - ``route``           ``"<METHOD> <rule>"``, rule-level not materialised.
  - ``patient_guid``    the single patient touched, or NULL for
    cohort/aggregate reads (``n_rows_returned`` is the denominator there).
  - ``n_rows_returned`` best-effort patient-data row count in the response.
  - ``response_status`` HTTP code returned (includes 4xx denials).
  - ``session_id``      SSO ``sid`` for the operator session.
  - ``event_type``      'read' default (admin-override machinery reserved).
  - ``admin_justification`` verbatim admin text; NULL for non-override rows.
  - ``spec_hash``       the analysis spec a group run executed (#654);
    NULL for single-patient rows, which predate the group tool.
  - ``payload_snapshot``    per-event JSONB (X1 tuple: role_guid/purpose/
    access_basis, plus any route-supplied detail — e.g. export_id/cohort_id).
"""
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db, JSONB


def _uuid():
    import uuid
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc)


class AnalyseAudit(db.Model):
    __tablename__ = "analyse_audit"

    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    timestamp = db.Column(
        db.DateTime(timezone=True), default=_now, nullable=False, index=True,
    )
    user_guid = db.Column(db.String(128), nullable=True, index=True)
    user_org_guids = db.Column(JSONB, nullable=False, default=list)
    route = db.Column(db.String(256), nullable=False, index=True)
    patient_guid = db.Column(UUID(as_uuid=False), nullable=True, index=True)
    n_rows_returned = db.Column(db.Integer, nullable=True)
    response_status = db.Column(db.Integer, nullable=False)
    session_id = db.Column(db.String(128), nullable=True, index=True)
    event_type = db.Column(
        db.String(32), nullable=False, default="read",
        server_default="read", index=True,
    )
    admin_justification = db.Column(db.Text, nullable=True)
    payload_snapshot = db.Column(JSONB, nullable=True)
    # #654: a GROUP run has no single patient, so patient_guid stays NULL and
    # the run is identified by the spec that produced it. Indexed because
    # "which runs used this spec" is the question an auditor actually asks;
    # the rest of the run detail lives in payload_snapshot, which exists for
    # exactly that.
    spec_hash = db.Column(db.String(80), nullable=True, index=True)

    def to_dict(self) -> dict:
        return {
            "guid": self.guid,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "user_guid": self.user_guid,
            "user_org_guids": self.user_org_guids,
            "route": self.route,
            "patient_guid": self.patient_guid,
            "n_rows_returned": self.n_rows_returned,
            "response_status": self.response_status,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "admin_justification": self.admin_justification,
            "payload_snapshot": self.payload_snapshot,
            "spec_hash": self.spec_hash,
        }
