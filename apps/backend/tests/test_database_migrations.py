from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from dualcode.database_migrations import upgrade_database
from dualcode.models import Base


def _url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_empty_database_upgrades_to_current_schema(tmp_path: Path) -> None:
    path = tmp_path / "new.db"
    upgrade_database(_url(path))

    engine = create_engine(_url(path))
    try:
        tables = set(inspect(engine).get_table_names())
        assert set(Base.metadata.tables).issubset(tables)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0002_legacy_columns"
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
        assert {"before_diff", "after_diff"}.issubset(
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
                "0002_legacy_columns"
            )
    finally:
        engine.dispose()
