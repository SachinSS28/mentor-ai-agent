"""The chat page on this Mac: only its own page can use it, it continues the Telegram
conversation, and it loads nothing from the internet."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from starlette.testclient import TestClient

from mentor import checkin
from mentor.channels import local

PAGE_ORIGIN = {"Origin": "http://localhost:8790"}


class FakeGraph:
    def get_state(self, config: dict[str, Any]) -> Any:
        assert config == {"configurable": {"thread_id": "telegram-1"}}
        messages = [
            HumanMessage(f"[Mon 02 Nov 2026, 17:30] What's on my list?{local.OFFLINE_SUFFIX}"),
            AIMessage("", tool_calls=[{"name": "list_open_tasks", "args": {}, "id": "c1"}]),
            ToolMessage("- T3 Send the abstract", tool_call_id="c1"),
            AIMessage("**T3** Send the abstract."),
        ]
        return SimpleNamespace(values={"messages": messages})


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    saved: dict[str, str] = {}
    asked: list[tuple[str, str]] = []

    def fake_ask(graph: Any, text: str, thread: str, sent_at: Any = None) -> str:
        asked.append((text, thread))
        return "**Done:** T4 added."

    monkeypatch.setattr(local, "thread_id", lambda: "telegram-1")
    monkeypatch.setattr(local, "online", lambda: False)
    monkeypatch.setattr(local, "ask", fake_ask)
    monkeypatch.setattr(local, "set_state", saved.__setitem__)

    @contextmanager
    def open_graph() -> Iterator[Any]:
        yield FakeGraph()

    with TestClient(local.build(open_graph), base_url="http://localhost:8790") as c:
        c.saved, c.asked = saved, asked  # type: ignore[attr-defined]
        yield c


def test_the_page_and_history_are_for_this_mac_only(client: TestClient) -> None:
    assert client.get("/").text.startswith("<!doctype html>")
    past = client.get("/history").json()
    assert past == {
        "online": False,
        "messages": [
            {"who": "you", "html": "What's on my list?"},
            {"who": "mentor", "html": "<b>T3</b> Send the abstract."},
        ],
    }
    # A web page using a look-alike name for this Mac (DNS rebinding) is refused.
    assert client.get("/history", headers={"Host": "evil.example:8790"}).status_code == 403


def test_messages_continue_the_telegram_conversation(client: TestClient) -> None:
    reply = client.post(
        "/message", json={"text": "Remind me to send the abstract"}, headers=PAGE_ORIGIN
    )
    assert reply.json() == {"html": "<b>Done:</b> T4 added."}
    # Sent while offline, so Mentor is told what can't be reached.
    sent = ("Remind me to send the abstract" + local.OFFLINE_SUFFIX, "telegram-1")
    assert client.asked == [sent]  # type: ignore[attr-defined]
    assert checkin.OWNER_LAST_MESSAGE in client.saved  # type: ignore[attr-defined]  # quiet check-ins
    assert client.post("/message", json={"text": "  "}, headers=PAGE_ORIGIN).status_code == 400


def test_other_pages_cant_send_mentor_instructions(client: TestClient) -> None:
    for headers in ({}, {"Origin": "https://evil.example"}, {"Origin": "null"}):
        assert (
            client.post("/message", json={"text": "Draft an email"}, headers=headers).status_code
            == 403
        )
    form = client.post("/message", data={"text": "x"}, headers=PAGE_ORIGIN)  # a plain HTML form
    assert form.status_code == 403
    assert client.asked == []  # type: ignore[attr-defined]


def test_the_page_works_without_internet() -> None:
    assert not re.search(r"https?://", local.PAGE)  # no scripts, styles or fonts from elsewhere
    assert "No internet" in local.PAGE
