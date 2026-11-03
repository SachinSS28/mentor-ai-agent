"""Feedback on a throwaway database: a note becomes a lesson in Mentor's instructions, and
forgetting it takes it out again. The model is stood in."""

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from tests.integration.conftest import NewDatabase

from mentor import feedback
from mentor.agent import prompts
from mentor.db.models import Feedback
from mentor.db.session import get_sessionmaker
from mentor.mcp_servers.mentor_core import memory


class LessonModel:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, messages: list[Any]) -> AIMessage:
        self.prompts.append(messages[-1].content)
        return AIMessage("Keep plans for the day under 80 words.\n(That's the lesson.)")


def test_a_note_becomes_a_lesson_mentor_follows(
    new_database: NewDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_database("mentor_test_feedback")
    model = LessonModel()
    monkeypatch.setattr(feedback, "get_chat_model", lambda role: model)
    monkeypatch.setattr(memory, "compute_embedding", lambda text, layer="profile": None)

    assert feedback.record("up", answer="Your calendar is clear.") is None  # no note, no lesson
    lesson = feedback.record("down", "too long", "Plan my day", "Here is a 400-word plan…")
    assert lesson == "Keep plans for the day under 80 words."  # first line only
    assert 'wrote: "too long"' in model.prompts[0] and "Plan my day" in model.prompts[0]

    with get_sessionmaker()() as session:
        rows = session.query(Feedback).order_by(Feedback.created_at).all()
    assert [(r.rating, r.note, r.lesson) for r in rows] == [
        ("up", None, None),
        ("down", "too long", "Keep plans for the day under 80 words."),
    ]
    assert feedback.lessons() == ["Keep plans for the day under 80 words."]
    instructions = prompts.mentor_system_prompt()
    assert "Lessons from" in instructions
    assert "- Keep plans for the day under 80 words." in instructions

    memory.forget(rows[1].memory_id)  # "forget the lesson about plans"
    assert feedback.lessons() == []
    assert "Lessons from" not in prompts.mentor_system_prompt()


def test_a_note_is_kept_as_written_when_the_model_cant_help(
    new_database: NewDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_database("mentor_test_feedback_fallback")

    def offline(role: str) -> Any:
        raise ConnectionError("Ollama isn't running")

    monkeypatch.setattr(feedback, "get_chat_model", offline)
    monkeypatch.setattr(memory, "compute_embedding", lambda text, layer="profile": None)
    lesson = feedback.record("down", "use my exact job title")
    assert lesson is not None and lesson.endswith("feedback on an answer: use my exact job title")
