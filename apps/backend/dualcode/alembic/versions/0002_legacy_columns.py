"""Upgrade databases created by the pre-Alembic startup patches.

Revision ID: 0002_legacy_columns
Revises: 0001_baseline
"""

import sqlalchemy as sa
from alembic import op


revision = "0002_legacy_columns"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {str(item["name"]) for item in inspector.get_columns(table)}


def upgrade() -> None:
    if "message_id" not in _columns("attachments"):
        with op.batch_alter_table("attachments") as batch_op:
            batch_op.add_column(sa.Column("message_id", sa.String(), nullable=True))
            batch_op.create_foreign_key(
                "fk_attachments_message_id_messages",
                "messages",
                ["message_id"],
                ["id"],
                ondelete="SET NULL",
            )
    attachment_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("attachments")}
    if "ix_attachments_message_id" not in attachment_indexes:
        op.create_index("ix_attachments_message_id", "attachments", ["message_id"])

    run_columns = _columns("agent_runs")
    if "before_diff" not in run_columns:
        op.add_column(
            "agent_runs",
            sa.Column("before_diff", sa.Text(), nullable=False, server_default=""),
        )
    if "after_diff" not in run_columns:
        op.add_column(
            "agent_runs",
            sa.Column("after_diff", sa.Text(), nullable=False, server_default=""),
        )

    job_columns = _columns("execution_jobs")
    if "evidence" not in job_columns:
        op.add_column(
            "execution_jobs",
            sa.Column("evidence", sa.Text(), nullable=False, server_default="{}"),
        )
    job_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("execution_jobs")}
    if "ix_execution_jobs_status" not in job_indexes:
        op.create_index("ix_execution_jobs_status", "execution_jobs", ["status"])


def downgrade() -> None:
    # These columns were already part of some pre-Alembic installations.
    # Removing them would destroy user data, so this compatibility revision is irreversible.
    pass
