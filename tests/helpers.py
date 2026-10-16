"""Test doubles shared by unit and integration tests."""

from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool


class ScriptedModel(GenericFakeChatModel):
    """Replies with a fixed script of messages; accepts tools like a real chat model."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self


def scripted(*replies: AIMessage | str) -> ScriptedModel:
    return ScriptedModel(messages=iter(replies))


def call(tool_name: str, call_id: str = "call-1", **args: Any) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": tool_name, "args": args, "id": call_id}])


@tool
def fake_open_tasks() -> str:
    """List open tasks."""
    return "- Send GST working to client (due Fri 23 Oct 17:00, P1)"
