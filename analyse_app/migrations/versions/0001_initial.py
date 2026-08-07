"""Initial schema — users + cohort + analyse_audit.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-07 12:00:00.000000

Scaffold + port (tickets #538/#539). Ships three tables:
  - ``users``         local SSO-caller mirror (SU bootstrap).
  - ``cohort``        persisted researcher cohort definitions (ported from
    dashboard.pdhc's ``cohort`` table, column-for-column).
  - ``analyse_audit`` read-side PDL kontroller log (ported from
    dashboard.pdhc's ``dashboard_audit``, renamed).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("guid", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("is_su", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("guid"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "cohort",
        sa.Column("guid", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("filter", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False),
        sa.Column("members", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False),
        sa.Column("n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("owner_label", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("guid"),
    )
    op.create_index("ix_cohort_created_at", "cohort", ["created_at"])

    op.create_table(
        "analyse_audit",
        sa.Column("guid", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("user_guid", sa.String(length=128), nullable=True),
        sa.Column("user_org_guids", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("route", sa.String(length=256), nullable=False),
        sa.Column("patient_guid", postgresql.UUID(as_uuid=False),
                  nullable=True),
        sa.Column("n_rows_returned", sa.Integer(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False,
                  server_default="read"),
        sa.Column("admin_justification", sa.Text(), nullable=True),
        sa.Column("payload_snapshot", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True),
        sa.PrimaryKeyConstraint("guid"),
    )
    op.create_index("ix_analyse_audit_timestamp", "analyse_audit",
                    ["timestamp"])
    op.create_index("ix_analyse_audit_user_guid", "analyse_audit",
                    ["user_guid"])
    op.create_index("ix_analyse_audit_route", "analyse_audit", ["route"])
    op.create_index("ix_analyse_audit_patient_guid", "analyse_audit",
                    ["patient_guid"])
    op.create_index("ix_analyse_audit_session_id", "analyse_audit",
                    ["session_id"])
    op.create_index("ix_analyse_audit_event_type", "analyse_audit",
                    ["event_type"])


def downgrade():
    op.drop_index("ix_analyse_audit_event_type", table_name="analyse_audit")
    op.drop_index("ix_analyse_audit_session_id", table_name="analyse_audit")
    op.drop_index("ix_analyse_audit_patient_guid", table_name="analyse_audit")
    op.drop_index("ix_analyse_audit_route", table_name="analyse_audit")
    op.drop_index("ix_analyse_audit_user_guid", table_name="analyse_audit")
    op.drop_index("ix_analyse_audit_timestamp", table_name="analyse_audit")
    op.drop_table("analyse_audit")
    op.drop_index("ix_cohort_created_at", table_name="cohort")
    op.drop_table("cohort")
    op.drop_table("users")
