"""Agent pieces against the local Postgres: the tasks tool and persistent conversation memory."""

from collections.abc import Iterator
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError
from tests.helpers import scripted

from mentor.agent.checkpoint import open_checkpointer
from mentor.agent.graph import ask, build_graph
from mentor.agent.tools import list_open_tasks, recall_memories
from mentor.db.models import Task
from mentor.db.session import get_engine, get_sessionmaker

SENT = datetime(2026, 10, 19, 8, 10, tzinfo=ZoneInfo("Asia/Hong_Kong"))


@pytest.fixture(scope="module", autouse=True)
def database() -> None:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("local Postgres is not running")


@pytest.fixture
def tasks() -> Iterator[str]:
    marker = f"[TEST {uuid4().hex[:6]}]"
    with get_sessionmaker()() as session:
        session.add_all(
            [
                Task(title=f"{marker} open", source="text", created_by="owner"),
                Task(title=f"{marker} done", source="text", created_by="owner", status="done"),
            ]
        )
        session.commit()
    yield marker
    with get_sessionmaker()() as session:
        session.execute(delete(Task).where(Task.title.startswith(marker)))
        session.commit()


def test_open_tasks_tool_lists_only_open_tasks(tasks: str) -> None:
    result = list_open_tasks.invoke({})
    assert f"{tasks} open" in result
    assert f"{tasks} done" not in result


def test_conversation_survives_a_restart(system_prompt: Any) -> None:
    thread = f"test-{uuid4().hex[:8]}"
    with open_checkpointer() as saver:
        ask(
            build_graph(scripted("Noted."), [], system_prompt, saver),
            "My office is in Central.",
            thread,
            SENT,
        )

    with open_checkpointer() as saver:  # a fresh process would reconnect the same way
        graph = build_graph(scripted("Central."), [], system_prompt, saver)
        ask(graph, "Where is my office?", thread, SENT)
        history = graph.get_state({"configurable": {"thread_id": thread}}).values["messages"]
        saver.delete_thread(thread)

    assert [m.text for m in history] == [
        "[Mon 19 Oct 2026, 08:10] My office is in Central.",
        "Noted.",
        "[Mon 19 Oct 2026, 08:10] Where is my office?",
        "Central.",
    ]


def test_recall_accepts_a_list_of_words_from_small_models() -> None:
    # Gemma sometimes calls recall_memories with query=[] meaning "everything".
    assert recall_memories.invoke({"query": []}) == recall_memories.invoke({"query": ""})
    assert recall_memories.invoke({"query": ["CV"]}) == recall_memories.invoke({"query": "CV"})
