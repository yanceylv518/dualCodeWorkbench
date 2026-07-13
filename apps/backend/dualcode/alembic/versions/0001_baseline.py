"""Current schema baseline.

Revision ID: 0001_baseline
"""

from alembic import op


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


TABLE_DDL = (
    """CREATE TABLE workspaces (
        id VARCHAR NOT NULL PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        path VARCHAR(1024) NOT NULL UNIQUE
    )""",
    """CREATE TABLE audit_logs (
        id VARCHAR NOT NULL PRIMARY KEY,
        workspace_id VARCHAR NOT NULL,
        thread_id VARCHAR,
        event VARCHAR(80) NOT NULL,
        detail TEXT NOT NULL,
        created_at DATETIME NOT NULL
    )""",
    """CREATE TABLE threads (
        id VARCHAR NOT NULL PRIMARY KEY,
        workspace_id VARCHAR NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        title VARCHAR(200) NOT NULL,
        state VARCHAR(17) NOT NULL
    )""",
    """CREATE TABLE project_governance (
        id VARCHAR NOT NULL PRIMARY KEY,
        workspace_id VARCHAR NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        product_goal TEXT NOT NULL,
        product_boundary TEXT NOT NULL,
        rules TEXT NOT NULL,
        deliverables TEXT NOT NULL,
        updated_at DATETIME NOT NULL
    )""",
    """CREATE TABLE task_contracts (
        id VARCHAR NOT NULL PRIMARY KEY,
        thread_id VARCHAR NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        goal TEXT NOT NULL,
        non_goals TEXT NOT NULL,
        acceptance TEXT NOT NULL,
        constraints TEXT NOT NULL,
        risks TEXT NOT NULL,
        status VARCHAR(32) NOT NULL,
        updated_at DATETIME NOT NULL
    )""",
    """CREATE TABLE handoff_packages (
        id VARCHAR NOT NULL PRIMARY KEY,
        workspace_id VARCHAR NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        thread_id VARCHAR NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        recipient VARCHAR(20) NOT NULL,
        purpose VARCHAR(30) NOT NULL,
        payload TEXT NOT NULL,
        status VARCHAR(20) NOT NULL,
        created_at DATETIME NOT NULL
    )""",
    """CREATE TABLE messages (
        id VARCHAR NOT NULL PRIMARY KEY,
        thread_id VARCHAR NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        role VARCHAR(20) NOT NULL,
        content TEXT NOT NULL,
        created_at DATETIME NOT NULL
    )""",
    """CREATE TABLE agent_runs (
        id VARCHAR NOT NULL PRIMARY KEY,
        thread_id VARCHAR NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        agent VARCHAR(30) NOT NULL,
        state VARCHAR(17) NOT NULL,
        output TEXT NOT NULL,
        before_diff TEXT NOT NULL,
        after_diff TEXT NOT NULL
    )""",
    """CREATE TABLE agent_sessions (
        id VARCHAR NOT NULL PRIMARY KEY,
        thread_id VARCHAR NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        agent VARCHAR(30) NOT NULL,
        external_session_id VARCHAR(255) NOT NULL,
        workspace_path VARCHAR(1024) NOT NULL
    )""",
    """CREATE TABLE file_changes (
        id VARCHAR NOT NULL PRIMARY KEY,
        thread_id VARCHAR NOT NULL REFERENCES threads(id),
        path VARCHAR(1024) NOT NULL,
        diff TEXT NOT NULL
    )""",
    """CREATE TABLE test_runs (
        id VARCHAR NOT NULL PRIMARY KEY,
        thread_id VARCHAR NOT NULL REFERENCES threads(id),
        command VARCHAR(500) NOT NULL,
        output TEXT NOT NULL,
        exit_code INTEGER NOT NULL
    )""",
    """CREATE TABLE approvals (
        id VARCHAR NOT NULL PRIMARY KEY,
        thread_id VARCHAR NOT NULL REFERENCES threads(id),
        action VARCHAR(60) NOT NULL,
        status VARCHAR(20) NOT NULL,
        reason TEXT NOT NULL
    )""",
    """CREATE TABLE attachments (
        id VARCHAR NOT NULL PRIMARY KEY,
        workspace_id VARCHAR NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        thread_id VARCHAR NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        message_id VARCHAR REFERENCES messages(id) ON DELETE SET NULL,
        name VARCHAR(255) NOT NULL,
        media_type VARCHAR(100) NOT NULL,
        size INTEGER NOT NULL,
        storage_key VARCHAR(255) NOT NULL UNIQUE
    )""",
    """CREATE TABLE execution_jobs (
        id VARCHAR NOT NULL PRIMARY KEY,
        approval_id VARCHAR NOT NULL REFERENCES approvals(id),
        workspace_id VARCHAR NOT NULL,
        thread_id VARCHAR NOT NULL REFERENCES threads(id),
        kind VARCHAR(60) NOT NULL,
        payload TEXT NOT NULL,
        idempotency_key VARCHAR(120) NOT NULL,
        status VARCHAR(24) NOT NULL,
        attempts INTEGER NOT NULL,
        last_error TEXT NOT NULL,
        evidence TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )""",
)

INDEX_DDL = (
    "CREATE INDEX ix_audit_logs_thread_id ON audit_logs (thread_id)",
    "CREATE INDEX ix_audit_logs_workspace_id ON audit_logs (workspace_id)",
    "CREATE INDEX ix_threads_workspace_id ON threads (workspace_id)",
    "CREATE UNIQUE INDEX ix_project_governance_workspace_id ON project_governance (workspace_id)",
    "CREATE UNIQUE INDEX ix_task_contracts_thread_id ON task_contracts (thread_id)",
    "CREATE INDEX ix_handoff_packages_thread_id ON handoff_packages (thread_id)",
    "CREATE INDEX ix_handoff_packages_workspace_id ON handoff_packages (workspace_id)",
    "CREATE INDEX ix_messages_thread_id ON messages (thread_id)",
    "CREATE INDEX ix_agent_runs_thread_id ON agent_runs (thread_id)",
    "CREATE INDEX ix_agent_sessions_external_session_id ON agent_sessions (external_session_id)",
    "CREATE INDEX ix_agent_sessions_thread_id ON agent_sessions (thread_id)",
    "CREATE INDEX ix_file_changes_thread_id ON file_changes (thread_id)",
    "CREATE INDEX ix_test_runs_thread_id ON test_runs (thread_id)",
    "CREATE INDEX ix_approvals_thread_id ON approvals (thread_id)",
    "CREATE INDEX ix_attachments_workspace_id ON attachments (workspace_id)",
    "CREATE INDEX ix_attachments_message_id ON attachments (message_id)",
    "CREATE INDEX ix_attachments_thread_id ON attachments (thread_id)",
    "CREATE UNIQUE INDEX ix_execution_jobs_approval_id ON execution_jobs (approval_id)",
    "CREATE UNIQUE INDEX ix_execution_jobs_idempotency_key ON execution_jobs (idempotency_key)",
    "CREATE INDEX ix_execution_jobs_workspace_id ON execution_jobs (workspace_id)",
    "CREATE INDEX ix_execution_jobs_status ON execution_jobs (status)",
    "CREATE INDEX ix_execution_jobs_thread_id ON execution_jobs (thread_id)",
)


def upgrade() -> None:
    for statement in TABLE_DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "execution_jobs",
        "attachments",
        "approvals",
        "test_runs",
        "file_changes",
        "agent_sessions",
        "agent_runs",
        "messages",
        "handoff_packages",
        "task_contracts",
        "project_governance",
        "threads",
        "audit_logs",
        "workspaces",
    ):
        op.drop_table(table)
