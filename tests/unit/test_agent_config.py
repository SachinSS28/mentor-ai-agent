from collections.abc import Iterator

import anthropic
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama

from mentor.agent.models import (
    ChatClaudeOnVertex,
    ModelsConfig,
    RoleConfig,
    get_chat_model,
    load_models_config,
)
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
    monkeypatch.setenv("MENTOR_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MENTOR_GCP_PROJECT", "test-project")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_cloud_server_runs_claude_opus_via_the_anthropic_api(cloud_settings: None) -> None:
    model = get_chat_model("orchestrator")
    assert type(model) is ChatAnthropic
    assert model.model == "claude-opus-5-5"
    payload = model._get_request_payload([HumanMessage("hi")])
    assert "temperature" not in payload  # current Claude models reject custom sampling
    assert payload["output_config"] == {"effort": "low"}
    # Stale thinking blocks from turns that slid out of the history window are dropped, not 400s.
    assert payload["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert payload["cache_control"] == {"type": "ephemeral"}
    assert payload["fallbacks"] == "default"
    assert set(payload["betas"]) == {
        "thinking-binding-controls-2026-08-01",
        "server-side-fallback-2026-07-01",
    }


def test_cloud_fast_role_is_haiku_without_fallbacks(cloud_settings: None) -> None:
    payload = get_chat_model("fast")._get_request_payload([HumanMessage("hi")])
    assert payload["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in payload  # Haiku 5.5 has no server-side refusal fallback


VERTEX = ModelsConfig(
    providers={"vertex": {"region": "global"}},
    roles={"orchestrator": RoleConfig(provider="vertex", model="claude-opus-5-5", effort="low")},
)


def test_vertex_route_signs_in_with_google_instead_of_a_key(cloud_settings: None) -> None:
    model = get_chat_model("orchestrator", VERTEX)
    assert isinstance(model, ChatClaudeOnVertex)
    client = model._client
    assert isinstance(client, anthropic.AnthropicVertex)
    assert (client.project_id, client.region) == ("test-project", "global")
    payload = model._get_request_payload([HumanMessage("hi")])
    assert payload["betas"] == ["thinking-binding-controls-2026-08-01"]
    assert "fallbacks" not in payload  # not offered on Vertex AI


def test_vertex_rejects_server_side_fallbacks(cloud_settings: None) -> None:
    config = ModelsConfig(
        providers={},
        roles={"orchestrator": RoleConfig(provider="vertex", model="claude-x", fallbacks=True)},
    )
    with pytest.raises(ValueError, match="fallbacks"):
        get_chat_model("orchestrator", config)


def test_claude_without_an_api_key_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MENTOR_ANTHROPIC_API_KEY", raising=False)
    no_env_file = Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr("mentor.agent.models.get_settings", lambda: no_env_file)
    config = ModelsConfig(
        providers={}, roles={"orchestrator": RoleConfig(provider="anthropic", model="claude-x")}
    )
    with pytest.raises(ValueError, match="MENTOR_ANTHROPIC_API_KEY"):
        get_chat_model("orchestrator", config)


def test_gemini_is_still_supported_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENTOR_GEMINI_API_KEY", "test-key")
    get_settings.cache_clear()
    config = ModelsConfig(
        providers={},
        roles={"orchestrator": RoleConfig(provider="gemini", model="gemini-x", temperature=None)},
    )
    assert isinstance(get_chat_model("orchestrator", config), ChatGoogleGenerativeAI)
    get_settings.cache_clear()


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


def test_system_prompt_uses_charter_and_persona_and_never_changes() -> None:
    charter = load_charter()
    persona = (get_settings().config_dir / "persona.md").read_text()

    prompt = build_system_prompt(charter, persona, "mac", "gemma4:e4b")

    assert prompt.startswith("You are Mentor, Sachin's personal chief of staff and coach.")
    # The clock is on each message, so the prompt is identical all conversation (cacheable).
    assert "starts with the date and time it was sent" in prompt
    assert "(Asia/Hong_Kong)" in prompt
    assert prompt == build_system_prompt(charter, persona, "mac", "gemma4:e4b")
    assert "point out overcommitment" in prompt  # direct coaching tone
    assert "switch to Hinglish or Hindi when Sachin writes that way" in prompt
    assert "You run on Sachin's Mac" in prompt
    assert "Your language model is gemma4:e4b." in prompt
    assert "{" not in prompt  # every placeholder filled


def test_mentor_knows_when_it_runs_on_the_cloud_server() -> None:
    persona = (get_settings().config_dir / "persona.md").read_text()

    prompt = build_system_prompt(load_charter(), persona, "cloud", "claude-opus-5-5")

    assert "always-on Google Cloud server" in prompt
    assert "Mac is closed or off" in prompt
    assert "not the language model underneath" in prompt


def test_bot_in_a_container_reaches_ollama_on_the_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENTOR_OLLAMA_URL", "http://host.docker.internal:11434")
    get_settings.cache_clear()
    model = get_chat_model("orchestrator")
    get_settings.cache_clear()
    assert isinstance(model, ChatOllama)
    assert model.base_url == "http://host.docker.internal:11434"


def test_gemma_gets_a_context_window_big_enough_for_mentor() -> None:
    # Ollama's 4,096-token default cut off Mentor's instructions once memories were recalled.
    model = get_chat_model("orchestrator")
    assert isinstance(model, ChatOllama)
    assert model.num_ctx is not None and model.num_ctx >= 32768
