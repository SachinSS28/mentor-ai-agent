"""Assembles the running agent from config, charter, model router, tools and checkpointer."""

from collections.abc import Iterator
from contextlib import contextmanager

from mentor.agent.checkpoint import open_checkpointer
from mentor.agent.graph import MentorGraph, build_graph
from mentor.agent.models import (
    CLAUDE_PROVIDERS,
    get_chat_model,
    load_models_config,
)
from mentor.agent.prompts import mentor_system_prompt
from mentor.agent.tools import TOOLS


@contextmanager
def open_agent() -> Iterator[MentorGraph]:
    config = load_models_config()
    role = config.roles["orchestrator"]
    chat_model = get_chat_model("orchestrator", config)
    with open_checkpointer() as checkpointer:
        yield build_graph(
            chat_model,
            TOOLS,
            # Built for each call, so a lesson from feedback counts from the next answer on.
            lambda: mentor_system_prompt(config),
            checkpointer,
            # The conversation can move between the Mac (Gemma) and the cloud (Claude).
            plain_text_history=role.provider not in CLAUDE_PROVIDERS,
        )
