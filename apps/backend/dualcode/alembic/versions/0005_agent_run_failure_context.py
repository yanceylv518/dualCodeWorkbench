"""Persist structured Agent failure diagnostics.

Revision ID: 0005_agent_run_failure_context
Revises: 0004_collaboration_review
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_agent_run_failure_context"
down_revision = "0004_collaboration_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {str(item["name"]) for item in sa.inspect(op.get_bind()).get_columns("agent_runs")}
    if "failure_kind" not in columns:
        op.add_column("agent_runs", sa.Column("failure_kind", sa.String(length=40), nullable=False, server_default=""))
    if "failure_context" not in columns:
        op.add_column("agent_runs", sa.Column("failure_context", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_column("failure_context")
        batch_op.drop_column("failure_kind")
