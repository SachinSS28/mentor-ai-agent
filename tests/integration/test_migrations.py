"""Runs the migrations against the local Postgres.

Start it with: docker compose -f infra/docker-compose.yml up -d
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

from mentor.db.models import Base
from mentor.db.session import get_engine

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    engine = get_engine()
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip("local Postgres is not running")
    command.upgrade(Config(toml_file=PYPROJECT), "head")
    yield engine


def test_migrations_create_every_table(engine: Engine) -> None:
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())


def test_migrations_match_the_models(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], "models changed without a migration: run alembic revision --autogenerate"


def test_database_rejects_unknown_task_status(engine: Engine) -> None:
    with engine.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO tasks (title, source, created_by, status) "
                "VALUES ('x', 'text', 'owner', 'someday')"
            )
        )
