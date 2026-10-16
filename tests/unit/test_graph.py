from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from tests.helpers import call, fake_open_tasks, scripted

from mentor.agent.graph import EMPTY_REPLY, ask, build_graph


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
    ask(graph, "Remember: the quarterly report is due Friday.", "t1")
    ask(graph, "What did I tell you?", "t1")

    messages = graph.get_state({"configurable": {"thread_id": "t1"}}).values["messages"]
    assert [m.text for m in messages if not isinstance(m, AIMessage)] == [
        "Remember: the quarterly report is due Friday.",
        "What did I tell you?",
    ]


def test_empty_model_answer_is_reported_not_silent(system_prompt: Any) -> None:
    graph = build_graph(scripted(AIMessage("")), [], system_prompt)
    assert ask(graph, "hi", "t1") == EMPTY_REPLY
