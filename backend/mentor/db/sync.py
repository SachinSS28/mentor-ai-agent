"""Copies Telegram conversations and memories between the Mac's and the cloud's database.

Only one copy of the bot answers at a time (mentor.channels.telegram.failover), so copying is
enough: conversation rows are only ever added, and a thread's newest checkpoint is its current
state wherever it was written. Memories merge by id and the newer edit wins; forgetting is an
edit (deleted_at), so it spreads too. Private memories never leave the database they're in.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import (
    Column,
    Engine,
    Integer,
    LargeBinary,
    MetaData,
    Row,
    Table,
    Text,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import JSONB, insert

from mentor.agent.checkpoint import CHECKPOINT_SCHEMA
from mentor.db.models import Memory

# The bot's threads; terminal chats stay on the machine they happened on.
SYNCED_THREADS = "telegram-%"

# LangGraph's own tables (PostgresSaver.setup), described here rather than read from the database
# each time: through the SSH tunnel that costs dozens of slow round trips. Primary keys first.
_langgraph = MetaData(schema=CHECKPOINT_SCHEMA)
CHECKPOINT_TABLES = {
    Table(
        "checkpoints",
        _langgraph,
        *(
            Column(k, Text, primary_key=True)
            for k in ("thread_id", "checkpoint_ns", "checkpoint_id")
        ),
        Column("parent_checkpoint_id", Text),
        Column("type", Text),
        Column("checkpoint", JSONB),
        Column("metadata", JSONB),
    ): ("thread_id", "checkpoint_ns", "checkpoint_id"),
    Table(
        "checkpoint_blobs",
        _langgraph,
        *(
            Column(k, Text, primary_key=True)
            for k in ("thread_id", "checkpoint_ns", "channel", "version")
        ),
        Column("type", Text),
        Column("blob", LargeBinary),
    ): ("thread_id", "checkpoint_ns", "channel", "version"),
    Table(
        "checkpoint_writes",
        _langgraph,
        *(
            Column(k, Text, primary_key=True)
            for k in ("thread_id", "checkpoint_ns", "checkpoint_id", "task_id")
        ),
        Column("idx", Integer, primary_key=True),
        Column("channel", Text),
        Column("type", Text),
        Column("blob", LargeBinary),
        Column("task_path", Text),
    ): ("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
}
# Embeddings are left out: none exist yet, and they can be recomputed from the fact.
MEMORY_COLUMNS = [c for c in Memory.__table__.c if c.name != "embedding"]
BATCH = 200


@dataclass
class SyncReport:
    copied: dict[str, int] = field(default_factory=dict)

    def add(self, what: str, count: int) -> None:
        if count:
            self.copied[what] = self.copied.get(what, 0) + count

    def __str__(self) -> str:
        if not self.copied:
            return "Already in sync."
        return ", ".join(f"{what}: {count}" for what, count in sorted(self.copied.items()))


def sync(local: Engine, cloud: Engine) -> SyncReport:
    """Bring both databases up to date with each other. Safe to run at any time, repeatedly."""
    report = SyncReport()
    for table, key in CHECKPOINT_TABLES.items():
        local_keys, cloud_keys = keys(local, table, key), keys(cloud, table, key)
        report.add(
            f"{table.name} to cloud", copy_rows(local, cloud, table, key, local_keys - cloud_keys)
        )
        report.add(
            f"{table.name} to Mac", copy_rows(cloud, local, table, key, cloud_keys - local_keys)
        )
    merge_memories(local, cloud, report)
    return report


def keys(engine: Engine, table: Table, key: Sequence[str]) -> set[tuple[Any, ...]]:
    statement = select(*(table.c[k] for k in key)).where(table.c.thread_id.like(SYNCED_THREADS))
    with engine.connect() as conn:
        return {tuple(row) for row in conn.execute(statement)}


def copy_rows(
    source: Engine, target: Engine, table: Table, key: Sequence[str], wanted: set[tuple[Any, ...]]
) -> int:
    if not wanted:
        return 0
    key_columns = tuple_(*(table.c[k] for k in key))
    copied = 0
    with source.connect() as reader, target.begin() as writer:
        for chunk in batches(sorted(wanted), BATCH):
            found = reader.execute(select(table).where(key_columns.in_(chunk))).mappings()
            rows = [dict(row) for row in found]
            # Same columns on both sides: LangGraph created both tables. (The driver doesn't
            # report row counts for batched inserts, so count what was sent.)
            writer.execute(insert(table).values(rows).on_conflict_do_nothing())
            copied += len(rows)
    return copied


def batches(items: Sequence[tuple[Any, ...]], size: int) -> Iterable[Sequence[tuple[Any, ...]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def merge_memories(local: Engine, cloud: Engine, report: SyncReport) -> None:
    local_rows, cloud_rows = shared_memories(local), shared_memories(cloud)
    report.add("memories to cloud", upsert_newer(cloud, newer(local_rows, cloud_rows)))
    report.add("memories to Mac", upsert_newer(local, newer(cloud_rows, local_rows)))


def shared_memories(engine: Engine) -> dict[Any, Row[Any]]:
    statement = select(*MEMORY_COLUMNS).where(Memory.private_flag.is_(False))
    with engine.connect() as conn:
        return {row.id: row for row in conn.execute(statement)}


def newer(source: dict[Any, Row[Any]], target: dict[Any, Row[Any]]) -> list[dict[str, Any]]:
    """Rows the target lacks or holds an older edit of."""
    return [
        dict(row._mapping)
        for memory_id, row in source.items()
        if memory_id not in target or row.updated_at > target[memory_id].updated_at
    ]


def upsert_newer(target: Engine, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statement = insert(Memory).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[Memory.id],
        set_={c.name: statement.excluded[c.name] for c in MEMORY_COLUMNS if c.name != "id"},
        where=Memory.updated_at < statement.excluded.updated_at,
    )
    with target.begin() as conn:
        conn.execute(statement)
    return len(rows)
