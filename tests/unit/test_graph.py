from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from tests.helpers import ScriptedModel, call, fake_open_tasks, scripted

from mentor.agent.graph import (
    EMPTY_REPLY,
    REFUSAL_REPLY,
    as_plain_text,
    ask,
    build_graph,
    recent_history,
)

SENT = datetime(2026, 10, 19, 8, 10, tzinfo=ZoneInfo("Asia/Hong_Kong"))


def test_answers_directly_without_tools(system_prompt: Any) -> None:
    graph = build_graph(scripted("Good morning, Sachin."), [fake_open_tasks], system_prompt)
    assert ask(graph, "hi", "t1") == "Good morning, Sachin."


def test_calls_tool_then_answers_with_its_result(system_prompt: Any) -> None:
    model = scripted(call("fake_open_tasks"), "You have one open task: Send quarterly report.")
    saver = InMemorySaver()
    graph = build_graph(model, [fake_open_tasks], system_prompt, saver)

    assert ask(graph, "What's open?", "t1") == "You have one open task: Send quarterly report."
    messages = graph.get_state({"configurable": {"thread_id": "t1"}}).values["messages"]
    tool_results = [m for m in messages if isinstance(m, ToolMessage)]
    assert tool_results and "Send quarterly report to client" in tool_results[0].text


def test_remembers_earlier_turns_in_the_same_thread(system_prompt: Any) -> None:
    graph = build_graph(
        scripted("Noted.", "You told me about the report."), [], system_prompt, InMemorySaver()
    )
    ask(graph, "Remember: the quarterly report is due Friday.", "t1", SENT)
    ask(graph, "What did I tell you?", "t1", SENT)

    messages = graph.get_state({"configurable": {"thread_id": "t1"}}).values["messages"]
    # Each message carries when it was sent, so the system prompt never has to change.
    assert [m.text for m in messages if not isinstance(m, AIMessage)] == [
        "[Mon 19 Oct 2026, 08:10] Remember: the quarterly report is due Friday.",
        "[Mon 19 Oct 2026, 08:10] What did I tell you?",
    ]


def test_empty_model_answer_is_reported_not_silent(system_prompt: Any) -> None:
    graph = build_graph(scripted(AIMessage("")), [], system_prompt)
    assert ask(graph, "hi", "t1") == EMPTY_REPLY


def test_history_sent_to_the_model_is_bounded_but_keeps_the_current_turn() -> None:
    old = [HumanMessage("old question " * 200), AIMessage("old answer " * 200)]
    recent = [HumanMessage("Remind me about the report"), AIMessage("Noted.")]
    current = [
        HumanMessage("What's open?"),
        call("fake_open_tasks"),
        ToolMessage("x" * 9000, tool_call_id="call-1"),
    ]

    sent = recent_history([*old, *recent, *current], max_tokens=500)

    assert sent == [*recent, *current]  # oldest turn dropped; huge current turn kept whole


def test_trimmed_history_never_starts_on_a_tool_result() -> None:
    turn = [
        HumanMessage("What's open?"),
        call("fake_open_tasks"),
        ToolMessage("y" * 400, tool_call_id="call-1"),
        AIMessage("One task."),
    ]

    sent = recent_history([*turn, HumanMessage("Thanks")], max_tokens=60)

    assert sent == [HumanMessage("Thanks")]


def test_declined_request_is_reported_not_silent(system_prompt: Any) -> None:
    refused = AIMessage("", response_metadata={"stop_reason": "refusal"})
    graph = build_graph(scripted(refused), [], system_prompt)
    assert ask(graph, "hi", "t1") == REFUSAL_REPLY


CLAUDE_TURN = AIMessage(
    content=[
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Checking your tasks."},
    ],
    tool_calls=[{"name": "fake_open_tasks", "args": {}, "id": "call-1"}],
)


def test_claude_turns_become_plain_text_for_other_models() -> None:
    plain = as_plain_text(CLAUDE_TURN)
    assert plain.content == "Checking your tasks."
    assert isinstance(plain, AIMessage) and plain.tool_calls == CLAUDE_TURN.tool_calls
    assert as_plain_text(HumanMessage("hi")) == HumanMessage("hi")


class RecordingModel(ScriptedModel):
    """A scripted model that also keeps what it was sent."""

    sent: list[list[BaseMessage]] = []

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.sent.append(messages)
        return super()._generate(messages, *args, **kwargs)


def test_stored_conversation_keeps_claude_blocks_while_gemma_gets_text(system_prompt: Any) -> None:
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "t1"}}
    stored = [
        HumanMessage("[earlier] What's open?"),
        CLAUDE_TURN,
        ToolMessage("- report", tool_call_id="call-1"),
        AIMessage("One task."),
    ]
    model = RecordingModel(messages=iter(["Still one task."]))
    graph = build_graph(model, [], system_prompt, saver, plain_text_history=True)
    graph.update_state(config, {"messages": stored}, as_node="agent")

    ask(graph, "And now?", "t1", SENT)

    sent_turn = model.sent[-1][2]  # after the system prompt and the first question
    assert sent_turn.content == "Checking your tasks."
    assert graph.get_state(config).values["messages"][1].content == CLAUDE_TURN.content
