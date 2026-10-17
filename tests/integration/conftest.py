"""Throwaway databases, so tests that write memories or conversations never touch real data.

The Mac's own database syncs with the cloud server, so a test row left there could spread.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from mentor.agent.checkpoint import CHECKPOINT_SCHEMA
from mentor.db.session import get_engine
from mentor.settings import get_settings

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

type NewDatabase = Callable[[str], Engine]


@contextmanager
def checkpointer(engine: Engine) -> Iterator[PostgresSaver]:
    url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with Connection.connect(
        url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options=f"-c search_path={CHECKPOINT_SCHEMA}",
    ) as conn:
        saver = PostgresSaver(conn)  # type: ignore[arg-type]
        saver.setup()
        yield saver


@pytest.fixture
def new_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[NewDatabase]:
    """Makes a fresh, migrated database; Mentor's own engine points at the last one made."""
    base = make_url(get_settings().database_url)
    admin = create_engine(base, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        admin.connect().close()
    except OperationalError:
        pytest.skip("local Postgres is not running")
    made: list[Engine] = []

    def make(name: str) -> Engine:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        url = base.set(database=name).render_as_string(hide_password=False)
        monkeypatch.setenv("MENTOR_DATABASE_URL", url)
        get_settings.cache_clear()
        get_engine.cache_clear()
        command.upgrade(Config(toml_file=PYPROJECT), "head")
        engine = create_engine(url, poolclass=NullPool)
        with checkpointer(engine):
            pass  # creates LangGraph's tables
        made.append(engine)
        return engine

    yield make
    for engine in made:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{engine.url.database}" WITH (FORCE)'))
    monkeypatch.undo()
    get_settings.cache_clear()
    get_engine.cache_clear()
