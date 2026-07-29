"""Add collaboration run and review finding tables together.

Collaboration runs have no writer until C5. The table is created now solely as
the review_findings foreign-key target because SQLite cannot add that foreign
key later without rebuilding and risking the findings table.

Revision ID: 0004_collaboration_review
Revises: 0003_memory_facts
"""

import sqlalchemy as sa
from alembic import op


revision = "0004_collaboration_review"
down_revision = "0003_memory_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "collaboration_runs" not in existing:
        op.create_table(
            "collaboration_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("thread_id", sa.String(), nullable=False),
            sa.Column("mode", sa.String(length=24), nullable=False),
            sa.Column("state", sa.String(length=32), nullable=False),
            sa.Column("current_agent", sa.String(length=30), nullable=True),
            sa.Column("round", sa.Integer(), nullable=False),
            sa.Column("max_rounds", sa.Integer(), nullable=False),
            sa.Column("budget_json", sa.Text(), nullable=False),
            sa.Column("base_sha", sa.String(length=64), nullable=True),
            sa.Column("snapshot_sha", sa.String(length=64), nullable=True),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["thread_id"], ["threads.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_collaboration_runs_workspace_id",
            "collaboration_runs",
            ["workspace_id"],
        )
        op.create_index(
            "ix_collaboration_runs_thread_id", "collaboration_runs", ["thread_id"]
        )
        op.create_index(
            "ix_collaboration_runs_state", "collaboration_runs", ["state"]
        )

    if "review_findings" not in existing:
        op.create_table(
            "review_findings",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("collaboration_run_id", sa.String(), nullable=True),
            sa.Column("round", sa.Integer(), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=False),
            sa.Column("severity", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("file", sa.String(length=1024), nullable=True),
            sa.Column("line", sa.String(length=100), nullable=True),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("acceptance", sa.Text(), nullable=False),
            sa.Column("source_handoff_id", sa.String(), nullable=False),
            sa.Column("resolved_by_snapshot_sha", sa.String(length=64), nullable=True),
            sa.ForeignKeyConstraint(
                ["collaboration_run_id"],
                ["collaboration_runs.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["source_handoff_id"],
                ["handoff_packages.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_review_findings_collaboration_run_id",
            "review_findings",
            ["collaboration_run_id"],
        )
        op.create_index(
            "ix_review_findings_status", "review_findings", ["status"]
        )
        op.create_index(
            "ix_review_findings_source_handoff_id",
            "review_findings",
            ["source_handoff_id"],
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "review_findings" in existing:
        op.drop_table("review_findings")
    if "collaboration_runs" in existing:
        op.drop_table("collaboration_runs")
