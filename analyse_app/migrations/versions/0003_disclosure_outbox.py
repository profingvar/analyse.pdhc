"""#1058: disclosure_outbox — disclosures waiting for record.pdhc.

One row per patient per research CSV export, written in the same transaction
as the export receipt (analyse_audit route='research_export'). Additive: a new
table, nothing existing is altered.

Revision ID: 0003_disclosure_outbox
Revises: 0002_audit_spec
Create Date: 2026-11-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_disclosure_outbox"
down_revision = "0002_audit_spec"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "disclosure_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guid", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_event_id", sa.String(length=200), nullable=False),
        sa.Column("patient_guid", sa.String(length=64), nullable=False),
        sa.Column("export_ref", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("record_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guid"),
        sa.UniqueConstraint("source_event_id"),
    )
    op.create_index("ix_disclosure_outbox_patient_guid", "disclosure_outbox",
                    ["patient_guid"])
    op.create_index("ix_disclosure_outbox_export_ref", "disclosure_outbox",
                    ["export_ref"])
    op.create_index("ix_disclosure_outbox_status", "disclosure_outbox",
                    ["status"])


def downgrade():
    op.drop_index("ix_disclosure_outbox_status", table_name="disclosure_outbox")
    op.drop_index("ix_disclosure_outbox_export_ref",
                  table_name="disclosure_outbox")
    op.drop_index("ix_disclosure_outbox_patient_guid",
                  table_name="disclosure_outbox")
    op.drop_table("disclosure_outbox")
