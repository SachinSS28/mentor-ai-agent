"""Assembles the running agent from config, charter, model router, tools and checkpointer."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from mentor.agent.checkpoint import open_checkpointer
from mentor.agent.graph import MentorGraph, build_graph
from mentor.agent.models import get_chat_model
from mentor.agent.prompts import build_system_prompt
from mentor.agent.tools import READ_ONLY_TOOLS
from mentor.charter import get_charter, owner_timezone
from mentor.settings import get_settings


@contextmanager
def open_agent() -> Iterator[MentorGraph]:
    charter = get_charter()
    persona = (get_settings().config_dir / "persona.md").read_text()
    tz = owner_timezone()

    def system_prompt() -> str:
        return build_system_prompt(charter, persona, datetime.now(tz))

    with open_checkpointer() as checkpointer:
        yield build_graph(
            get_chat_model("orchestrator"), READ_ONLY_TOOLS, system_prompt, checkpointer
        )
