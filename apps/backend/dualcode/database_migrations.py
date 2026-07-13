from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


BASELINE_REVISION = "0001_baseline"


def _sync_url(database_url: str) -> str:
    return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)


def upgrade_database(database_url: str) -> None:
    """Upgrade a new or pre-Alembic database to the current schema."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("alembic")))
    engine = create_engine(_sync_url(database_url))
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            config.attributes["connection"] = connection
            if "workspaces" in tables and "alembic_version" not in tables:
                command.stamp(config, BASELINE_REVISION)
            command.upgrade(config, "head")
    finally:
        engine.dispose()
