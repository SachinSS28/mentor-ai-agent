"""Agent pieces against the local Postgres: the tasks tool and persistent conversation memory."""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError
from tests.helpers import scripted

from mentor.agent.checkpoint import open_checkpointer
from mentor.agent.graph import ask, build_graph
from mentor.agent.tools import list_open_tasks
from mentor.db.models import Task
from mentor.db.session import get_engine, get_sessionmaker


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
            "My office is in BKC.",
            thread,
        )

    with open_checkpointer() as saver:  # a fresh process would reconnect the same way
        graph = build_graph(scripted("BKC."), [], system_prompt, saver)
        ask(graph, "Where is my office?", thread)
        history = graph.get_state({"configurable": {"thread_id": thread}}).values["messages"]
        saver.delete_thread(thread)

    assert [m.text for m in history] == [
        "My office is in BKC.",
        "Noted.",
        "Where is my office?",
        "BKC.",
    ]
