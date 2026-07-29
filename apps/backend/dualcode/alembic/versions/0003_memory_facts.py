"""Add shared collaboration memory facts.

Revision ID: 0003_memory_facts
Revises: 0002_legacy_columns
"""

import sqlalchemy as sa
from alembic import op


revision = "0003_memory_facts"
down_revision = "0002_legacy_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "memory_facts" in inspector.get_table_names():
        return

    op.create_table(
        "memory_facts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("supersedes_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["memory_facts.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["threads.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_facts_workspace_id", "memory_facts", ["workspace_id"])
    op.create_index("ix_memory_facts_thread_id", "memory_facts", ["thread_id"])
    op.create_index("ix_memory_facts_confidence", "memory_facts", ["confidence"])


def downgrade() -> None:
    op.drop_table("memory_facts")
