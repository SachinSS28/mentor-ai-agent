"""Conversation memory: LangGraph checkpoints in Postgres, in their own `langgraph` schema."""

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from mentor.settings import get_settings

CHECKPOINT_SCHEMA = "langgraph"  # created by an Alembic migration


@contextmanager
def open_checkpointer() -> Iterator[PostgresSaver]:
    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    connection_kwargs = {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": f"-c search_path={CHECKPOINT_SCHEMA}",
    }
    with ConnectionPool(
        url, connection_class=Connection[DictRow], kwargs=connection_kwargs, min_size=1, max_size=4
    ) as pool:
        saver = PostgresSaver(pool)
        saver.setup()
        yield saver
