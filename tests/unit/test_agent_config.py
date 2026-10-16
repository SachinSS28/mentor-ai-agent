from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from langchain_ollama import ChatOllama

from mentor.agent.models import ModelsConfig, RoleConfig, get_chat_model, load_models_config
from mentor.agent.prompts import build_system_prompt
from mentor.charter import load_charter
from mentor.settings import get_settings


def test_orchestrator_runs_on_local_gemma_until_bedrock() -> None:
    model = get_chat_model("orchestrator")
    assert isinstance(model, ChatOllama)
    assert model.model == "gemma4:e4b"


def test_tool_using_roles_keep_the_models_default_thinking() -> None:
    # Gemma 4 answers blank after tool calls when thinking is forced off.
    assert all(role.reasoning is not False for role in load_models_config().roles.values())


def test_unconfigured_role_fails_clearly() -> None:
    config = ModelsConfig(providers={}, roles={})
    with pytest.raises(ValueError, match="bulk"):
        get_chat_model("bulk", config)


def test_bedrock_is_not_wired_up_yet() -> None:
    config = ModelsConfig(
        providers={}, roles={"orchestrator": RoleConfig(provider="bedrock", model="x")}
    )
    with pytest.raises(NotImplementedError, match="step 0.4"):
        get_chat_model("orchestrator", config)


def test_system_prompt_uses_charter_persona_and_local_time() -> None:
    charter = load_charter()
    persona = (get_settings().config_dir / "persona.md").read_text()
    now = datetime(2026, 10, 16, 7, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))

    prompt = build_system_prompt(charter, persona, now)

    assert prompt.startswith("You are Mentor, Sachin's personal chief of staff and coach.")
    assert "Friday 16 October 2026, 07:00 (Asia/Hong_Kong)" in prompt
    assert "point out overcommitment" in prompt  # direct coaching tone
    assert "switch to Hinglish or Hindi when Sachin writes that way" in prompt
    assert "{" not in prompt  # every placeholder filled
