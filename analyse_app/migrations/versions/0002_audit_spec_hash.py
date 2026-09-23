"""#654: analyse_audit.spec_hash — identify a GROUP run by its spec.

A group run has no single patient, so patient_guid stays NULL and the run is
identified by the spec that produced it. Indexed because "which runs used
this spec" is the question an auditor asks; the rest of the run detail goes
in the existing payload_snapshot JSONB.

Additive and nullable. Rows written before today carry a patient_guid and no
spec_hash; rows written after carry the reverse. A reader of this table must
not assume either column is always present — see analyse.pdhc ADR-0007.

Revision ID: 0002_audit_spec_hash
Revises: 0001_initial
Create Date: 2026-09-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_audit_spec"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analyse_audit",
                  sa.Column("spec_hash", sa.String(length=80), nullable=True))
    op.create_index("ix_analyse_audit_spec_hash", "analyse_audit",
                    ["spec_hash"])


def downgrade():
    op.drop_index("ix_analyse_audit_spec_hash", table_name="analyse_audit")
    op.drop_column("analyse_audit", "spec_hash")
