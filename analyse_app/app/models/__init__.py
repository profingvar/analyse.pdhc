"""analyse.pdhc models.

The group/population + federated-events half of the old dashboard. Owns
three tables:

  - ``users``         local mirror of SSO callers (SU bootstrap via
    ``flask create-su``; ``_upsert_local_user`` on SSO login).
  - ``cohort``        persisted researcher cohort definitions (ported from
    dashboard.pdhc — filter + member set as JSONB, cross-worker/restart safe).
  - ``analyse_audit`` read-side PDL kontroller log (see ``models/audit.py``).

``db`` + ``JSONB`` live in ``app.extensions`` and are re-exported here so the
ported dashboard modules keep working with ``from app.models import db``.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db, JSONB
from app.models.audit import AnalyseAudit


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc)


class User(db.Model):
    """Local mirror of an SSO caller. Populated by ``_upsert_local_user`` and
    the ``flask create-su`` bootstrap (Rule 23). Authorization is never made
    from this table — the SSO blob is the source of truth every request."""
    __tablename__ = "users"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    username = db.Column(db.String(128), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_su = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), default=_now, nullable=False,
    )


class Cohort(db.Model):
    """Persisted researcher cohort definition (ported from dashboard.pdhc,
    Phase-4.5).

    Filter and member set are stored as JSONB so the cohort can be reused
    across gunicorn workers and across process restarts. The owner label is
    the SSO-blob user_guid-or-email string; we don't FK to ``users`` because
    service-key callers could write cohorts too without appearing in users.
    """
    __tablename__ = "cohort"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    filter = db.Column(JSONB, nullable=False)
    members = db.Column(JSONB, nullable=False, default=list)
    n = db.Column(db.Integer, nullable=False, default=0)
    owner_label = db.Column(db.String(256), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), default=_now, nullable=False,
    )


__all__ = ["db", "JSONB", "User", "Cohort", "AnalyseAudit"]
