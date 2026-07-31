from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from dualcode import database_migrations
from dualcode.database_migrations import upgrade_database
from dualcode.models import Base


def _url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _downgrade_database(path: Path, revision: str) -> None:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(database_migrations.__file__).with_name("alembic")),
    )
    engine = create_engine(_url(path))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, revision)
    finally:
        engine.dispose()


def test_empty_database_upgrades_to_current_schema(tmp_path: Path) -> None:
    path = tmp_path / "new.db"
    upgrade_database(_url(path))

    engine = create_engine(_url(path))
    try:
        tables = set(inspect(engine).get_table_names())
        assert set(Base.metadata.tables).issubset(tables)
        memory_indexes = {
            item["name"] for item in inspect(engine).get_indexes("memory_facts")
        }
        assert {
            "ix_memory_facts_workspace_id",
            "ix_memory_facts_thread_id",
            "ix_memory_facts_confidence",
        }.issubset(memory_indexes)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0005_agent_run_failure_context"
            )
    finally:
        engine.dispose()


def test_memory_facts_migration_downgrades_without_touching_existing_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "downgrade.db"
    upgrade_database(_url(path))
    engine = create_engine(_url(path))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workspaces (id, name, path) "
                    "VALUES ('workspace-1', 'Existing project', '/existing')"
                )
            )
            connection.execute(
                text(
                    """INSERT INTO memory_facts (
                        id, workspace_id, thread_id, kind, content_json,
                        source, confidence, commit_sha, supersedes_id,
                        created_at, invalidated_at
                    ) VALUES (
                        'fact-1', 'workspace-1', NULL, 'requirement',
                        '{"content":"goal"}', 'user', 'confirmed',
                        NULL, NULL, CURRENT_TIMESTAMP, NULL
                    )"""
                )
            )
    finally:
        engine.dispose()

    _downgrade_database(path, "0002_legacy_columns")

    engine = create_engine(_url(path))
    try:
        assert "memory_facts" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT name FROM workspaces WHERE id = 'workspace-1'")
            ) == "Existing project"
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0002_legacy_columns"
            )
    finally:
        engine.dispose()


def test_collaboration_review_migration_downgrades_without_touching_existing_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "collaboration-downgrade.db"
    upgrade_database(_url(path))
    engine = create_engine(_url(path))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workspaces (id, name, path) "
                    "VALUES ('workspace-1', 'Existing project', '/existing')"
                )
            )
    finally:
        engine.dispose()

    _downgrade_database(path, "0003_memory_facts")

    engine = create_engine(_url(path))
    try:
        tables = set(inspect(engine).get_table_names())
        assert "collaboration_runs" not in tables
        assert "review_findings" not in tables
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT name FROM workspaces WHERE id = 'workspace-1'")
            ) == "Existing project"
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0003_memory_facts"
            )
    finally:
        engine.dispose()


def test_pre_patch_database_preserves_data_and_adds_columns(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    engine = create_engine(_url(path))
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE workspaces (id VARCHAR PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE messages (id VARCHAR PRIMARY KEY)"))
            connection.execute(
                text(
                    """CREATE TABLE attachments (
                        id VARCHAR PRIMARY KEY, workspace_id VARCHAR NOT NULL,
                        thread_id VARCHAR NOT NULL, name VARCHAR NOT NULL,
                        media_type VARCHAR NOT NULL, size INTEGER NOT NULL,
                        storage_key VARCHAR NOT NULL
                    )"""
                )
            )
            connection.execute(
                text(
                    """CREATE TABLE agent_runs (
                        id VARCHAR PRIMARY KEY, thread_id VARCHAR NOT NULL,
                        agent VARCHAR NOT NULL, state VARCHAR NOT NULL, output TEXT NOT NULL
                    )"""
                )
            )
            connection.execute(
                text(
                    """CREATE TABLE execution_jobs (
                        id VARCHAR PRIMARY KEY, status VARCHAR NOT NULL
                    )"""
                )
            )
            connection.execute(
                text(
                    "INSERT INTO attachments VALUES "
                    "('attachment-1', 'workspace-1', 'thread-1', 'note.txt', "
                    "'text/plain', 4, 'stored-note')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO agent_runs VALUES "
                    "('run-1', 'thread-1', 'codex', 'COMPLETED', 'done')"
                )
            )
            connection.execute(
                text("INSERT INTO execution_jobs VALUES ('job-1', 'FAILED')")
            )
    finally:
        engine.dispose()

    upgrade_database(_url(path))

    engine = create_engine(_url(path))
    try:
        inspector = inspect(engine)
        assert "message_id" in {column["name"] for column in inspector.get_columns("attachments")}
        assert {
            "before_diff",
            "after_diff",
            "failure_kind",
            "failure_context",
        }.issubset(
            {column["name"] for column in inspector.get_columns("agent_runs")}
        )
        assert "evidence" in {
            column["name"] for column in inspector.get_columns("execution_jobs")
        }
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT name FROM attachments WHERE id = 'attachment-1'")
            ) == "note.txt"
            assert connection.scalar(
                text("SELECT output FROM agent_runs WHERE id = 'run-1'")
            ) == "done"
            assert connection.scalar(
                text("SELECT evidence FROM execution_jobs WHERE id = 'job-1'")
            ) == "{}"
    finally:
        engine.dispose()


def test_post_patch_database_is_stamped_without_data_loss(tmp_path: Path) -> None:
    path = tmp_path / "patched.db"
    engine = create_engine(_url(path))
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workspaces (id, name, path) "
                    "VALUES ('workspace-1', 'Existing project', '/existing')"
                )
            )
    finally:
        engine.dispose()

    upgrade_database(_url(path))

    engine = create_engine(_url(path))
    try:
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT name FROM workspaces WHERE id = 'workspace-1'")
            ) == "Existing project"
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0005_agent_run_failure_context"
            )
    finally:
        engine.dispose()
