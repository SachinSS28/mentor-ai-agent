"""Assembles the running agent from config, charter, model router, tools and checkpointer."""

from collections.abc import Iterator
from contextlib import contextmanager

from mentor.agent.checkpoint import open_checkpointer
from mentor.agent.graph import MentorGraph, build_graph
from mentor.agent.models import (
    CLAUDE_PROVIDERS,
    ModelsConfig,
    get_chat_model,
    load_models_config,
)
from mentor.agent.prompts import build_system_prompt
from mentor.agent.tools import TOOLS
from mentor.charter import get_charter, owner_profile_text
from mentor.settings import get_settings


def mentor_system_prompt(config: ModelsConfig | None = None) -> str:
    """Mentor's instructions with the charter and the owner's profile filled in."""
    settings = get_settings()
    persona = (settings.config_dir / "persona.md").read_text()
    role = (config or load_models_config()).roles["orchestrator"]
    return build_system_prompt(
        get_charter(), persona, settings.host, role.model, owner_profile_text()
    )


@contextmanager
def open_agent() -> Iterator[MentorGraph]:
    config = load_models_config()
    role = config.roles["orchestrator"]
    chat_model = get_chat_model("orchestrator", config)
    system_prompt = mentor_system_prompt(config)

    with open_checkpointer() as checkpointer:
        yield build_graph(
            chat_model,
            TOOLS,
            lambda: system_prompt,
            checkpointer,
            # The conversation can move between the Mac (Gemma) and the cloud (Claude).
            plain_text_history=role.provider not in CLAUDE_PROVIDERS,
        )
