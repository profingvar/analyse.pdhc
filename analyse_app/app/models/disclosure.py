"""disclosure_outbox — one row per patient per disclosure, until record.pdhc has it (#1058).

A disclosure (utlämnande) must never be lost and must never block or fail the
export it describes. So the AuditEvent is built and stored HERE, in analyse's
own database, in the same transaction as the export receipt
(``analyse_audit`` route='research_export'). Sending to record.pdhc happens
after the commit and may fail; the row then stays ``pending`` with the error
name and an attempt count, and ``flask send-disclosures`` retries it.
record.pdhc dedupes on (source service, ``source_event_id``), so a resend is
harmless.

Columns:
  - ``source_event_id`` this service's stable id for the export+patient,
    ``<export_id>:<patient_guid>``. Unique: a second write for the same pair
    is a bug, not a retry.
  - ``patient_guid``   the eps patient GUID (Rule 18).
  - ``export_ref``     the export_id of the receipt this row belongs to.
  - ``payload``        the complete FHIR R5 AuditEvent, frozen at write time.
  - ``status``         'pending' | 'sent'.
  - ``attempts``, ``last_error`` — last_error is a NAME (``http_403``,
    ``record_not_configured: RECORD_BASE_URL unset``), never a body.
  - ``record_id``      record.pdhc's id for the stored AuditEvent, once sent.
"""
from datetime import datetime, timezone
import uuid

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db, JSONB


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc)


class DisclosureOutbox(db.Model):
    __tablename__ = "disclosure_outbox"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    guid = db.Column(UUID(as_uuid=False), nullable=False, unique=True,
                     default=_uuid)
    source_event_id = db.Column(db.String(200), nullable=False, unique=True)
    patient_guid = db.Column(db.String(64), nullable=False, index=True)
    export_ref = db.Column(db.String(64), nullable=False, index=True)
    payload = db.Column(JSONB, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending",
                       server_default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0,
                         server_default="0")
    last_error = db.Column(db.Text, nullable=True)
    record_id = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           default=_now)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
