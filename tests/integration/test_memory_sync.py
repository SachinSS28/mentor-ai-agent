"""Long-term memory, heartbeats and the Mac/cloud sync, on throwaway databases."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, MetaData, Table, func, select, update
from tests.helpers import fake_open_tasks, scripted
from tests.integration.conftest import NewDatabase, checkpointer

from mentor.agent.graph import ask, build_graph
from mentor.db.heartbeat import beat, silence
from mentor.db.models import Memory
from mentor.db.sync import CHECKPOINT_TABLES, sync
from mentor.mcp_servers.mentor_core import memory


def converse(engine: Engine, thread: str, *replies: str) -> None:
    """One turn per scripted reply, saved in `engine`'s database."""
    with checkpointer(engine) as saver:
        graph = build_graph(scripted(*replies), [fake_open_tasks], lambda: "You are Mentor.", saver)
        for reply in replies:
            ask(graph, f"Say: {reply}", thread)


def messages_in(engine: Engine, thread: str) -> list[str]:
    with checkpointer(engine) as saver:
        state = saver.get_tuple({"configurable": {"thread_id": thread}})
    return [m.text for m in state.checkpoint["channel_values"]["messages"]] if state else []


def facts(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.scalars(select(Memory.fact).where(Memory.deleted_at.is_(None))))


def test_remember_recall_correct_and_forget(new_database: NewDatabase) -> None:
    new_database("mentor_test_memory")
    cv = memory.remember("CV: 10 years in product management; MBA 2015", "profile")
    memory.remember("Prefers meetings after 10:00", "preference")
    secret = memory.remember("Keeps a private journal", "profile", private=True)

    found = {m.id for m in memory.recall("product CV", include_private=False)}
    assert cv.id in found and secret.id not in found
    assert secret.id in {m.id for m in memory.recall("", include_private=True)}

    memory.remember("CV: 11 years in product management; MBA 2015", replaces=cv.id)
    cv_facts = {m.fact for m in memory.recall("CV", include_private=False)}
    assert "CV: 11 years in product management; MBA 2015" in cv_facts
    assert "CV: 10 years in product management; MBA 2015" not in cv_facts

    assert memory.forget(cv.id) is not None
    assert cv.id not in {m.id for m in memory.recall("", include_private=True)}
    assert memory.forget(cv.id) is None
    with pytest.raises(LookupError):
        memory.remember("A correction of nothing", replaces=uuid.uuid4())


def test_heartbeat_silence_uses_the_database_clock(new_database: NewDatabase) -> None:
    db = new_database("mentor_test_heartbeat")
    assert silence(db, "cloud") is None
    beat(db, "cloud")
    beat(db, "cloud")
    quiet = silence(db, "cloud")
    assert quiet is not None and quiet < timedelta(seconds=10)
    assert silence(db, "mac") is None


def test_the_mac_carries_on_the_cloud_conversation_and_hands_it_back(
    new_database: NewDatabase,
) -> None:
    mac, cloud = new_database("mentor_test_mac"), new_database("mentor_test_cloud")
    converse(cloud, "telegram-42", "Hello from the cloud")
    sync(mac, cloud)

    converse(mac, "telegram-42", "Hello from the Mac")  # the cloud is down
    on_mac = messages_in(mac, "telegram-42")
    assert "Hello from the cloud" in on_mac and "Hello from the Mac" in on_mac

    sync(mac, cloud)
    assert messages_in(cloud, "telegram-42") == on_mac
    assert str(sync(mac, cloud)) == "Already in sync."


def test_sync_copies_every_column_langgraph_has(new_database: NewDatabase) -> None:
    # sync.py describes LangGraph's tables itself; a LangGraph upgrade must not add a column
    # that would silently stay behind.
    db = new_database("mentor_test_columns")
    for table in CHECKPOINT_TABLES:
        actual = Table(table.name, MetaData(schema=table.schema), autoload_with=db)
        assert set(actual.c.keys()) == set(table.c.keys()), table.name
        assert set(actual.primary_key.columns.keys()) == set(table.primary_key.columns.keys())


def test_terminal_chats_stay_on_their_machine(new_database: NewDatabase) -> None:
    mac, cloud = new_database("mentor_test_mac"), new_database("mentor_test_cloud")
    converse(mac, "terminal-abc", "Just between us")
    sync(mac, cloud)
    assert messages_in(cloud, "terminal-abc") == []


def test_memories_merge_with_the_newest_edit_winning(new_database: NewDatabase) -> None:
    mac, cloud = new_database("mentor_test_mac"), new_database("mentor_test_cloud")
    # Mentor's own engine points at the last database made, the cloud.
    home = memory.remember("Lives in Hong Kong")
    hidden = memory.remember("Keeps a private journal", private=True)
    sync(mac, cloud)
    assert facts(mac) == {"Lives in Hong Kong"}  # private memories stay where they are

    with mac.begin() as conn:  # corrected on the Mac while it answered
        conn.execute(
            update(Memory)
            .where(Memory.id == home.id)
            .values(fact="Lives in Kowloon, Hong Kong", updated_at=func.now())
        )
    sync(mac, cloud)
    assert facts(cloud) == {"Lives in Kowloon, Hong Kong", "Keeps a private journal"}

    memory.forget(home.id)  # forgotten on the cloud
    sync(mac, cloud)
    assert facts(mac) == set()
    assert hidden.id is not None
