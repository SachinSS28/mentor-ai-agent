from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama

from mentor.agent.models import ModelsConfig, RoleConfig, get_chat_model, load_models_config
from mentor.agent.prompts import build_system_prompt
from mentor.charter import load_charter
from mentor.settings import Settings, get_settings


def test_orchestrator_runs_on_local_gemma_on_the_mac() -> None:
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


@pytest.fixture
def cloud_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MENTOR_MODELS_FILE", "models.cloud.yaml")
    monkeypatch.setenv("MENTOR_GEMINI_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_cloud_server_runs_the_orchestrator_on_gemini(cloud_settings: None) -> None:
    model = get_chat_model("orchestrator")
    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.model.startswith("gemini-")
    assert model.temperature is None  # current Gemini Flash models reject custom sampling
    assert model.reasoning_effort == "low"


def test_gemini_without_an_api_key_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MENTOR_GEMINI_API_KEY", raising=False)
    # Ignore the developer's .env, which may hold a real key.
    no_env_file = Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr("mentor.agent.models.get_settings", lambda: no_env_file)
    config = ModelsConfig(
        providers={}, roles={"orchestrator": RoleConfig(provider="gemini", model="gemini-x")}
    )
    with pytest.raises(ValueError, match="MENTOR_GEMINI_API_KEY"):
        get_chat_model("orchestrator", config)


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

    prompt = build_system_prompt(charter, persona, now, "mac", "gemma4:e4b")

    assert prompt.startswith("You are Mentor, Sachin's personal chief of staff and coach.")
    assert "Friday 16 October 2026, 07:00 (Asia/Hong_Kong)" in prompt
    assert "point out overcommitment" in prompt  # direct coaching tone
    assert "switch to Hinglish or Hindi when Sachin writes that way" in prompt
    assert "You run on Sachin's Mac" in prompt
    assert "Your language model is gemma4:e4b." in prompt
    assert "{" not in prompt  # every placeholder filled


def test_mentor_knows_when_it_runs_on_the_cloud_server() -> None:
    persona = (get_settings().config_dir / "persona.md").read_text()
    now = datetime(2026, 10, 16, 7, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))

    prompt = build_system_prompt(load_charter(), persona, now, "cloud", "gemini-3.8-flash")

    assert "always-on Google Cloud server" in prompt
    assert "Mac is closed or off" in prompt
    assert "not the language model underneath" in prompt
