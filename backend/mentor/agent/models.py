"""Model router: maps each role to a model from config/models.yaml (or MENTOR_MODELS_FILE)."""

from functools import cached_property
from pathlib import Path
from typing import Any, Literal

import anthropic
import yaml
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from pydantic import BaseModel

from mentor.settings import get_settings

Role = Literal["orchestrator", "fast", "bulk", "embed"]
# anthropic: Claude via the Anthropic API (API key). vertex: Claude via Google Cloud's Vertex AI,
# billed to the Google Cloud account and signed in with the server's Google identity.
Provider = Literal["ollama", "gemini", "anthropic", "vertex", "bedrock"]
CLAUDE_PROVIDERS = ("anthropic", "vertex")

# Claude keeps thinking blocks valid only while the earlier conversation is unchanged. Mentor
# sends a sliding window of recent history, so blocks from turns that slid out are dropped by
# the API (unbilled) instead of failing the request. Blocks within the current turn stay valid.
CLAUDE_THINKING = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
THINKING_BINDING_BETA = "thinking-binding-controls-2026-08-01"
# If Claude's safety classifiers decline a request, the API re-runs it on the model Anthropic
# recommends for that category instead of returning a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class RoleConfig(BaseModel):
    provider: Provider
    model: str
    # None sends no temperature: Gemini 3.6+ and current Claude models reject custom sampling.
    temperature: float | None = 0.3
    # Ollama only. None keeps the model's default. Gemma 4 returns empty answers after tool
    # calls when thinking is switched off, so don't set false for tool-using roles.
    reasoning: bool | None = None
    # Ollama only: the context window in tokens. Ollama's default (4,096) silently cuts off the
    # start of longer requests, which is where Mentor's instructions are.
    num_ctx: int | None = None
    # Gemini only: how hard the model thinks before answering ("low" is quick and cheap).
    thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    # Claude only. Effort controls how much the model thinks, so it is the main cost lever.
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    # Claude only: room for the model's thinking as well as its reply.
    max_tokens: int = 16000
    # Claude via the Anthropic API only (not Vertex): retry declined requests on Anthropic's
    # recommended fallback model.
    fallbacks: bool = False


class ModelsConfig(BaseModel):
    providers: dict[str, dict[str, str]]
    roles: dict[Role, RoleConfig]


def load_models_config(path: Path | None = None) -> ModelsConfig:
    settings = get_settings()
    path = path or settings.config_dir / settings.models_file
    return ModelsConfig.model_validate(yaml.safe_load(path.read_text()))


class ChatClaudeOnVertex(ChatAnthropic):
    """Claude through Google Cloud's Vertex AI: the same requests as ChatAnthropic, sent with
    Anthropic's AnthropicVertex client, which signs in with Google credentials (the server's
    service account, or `gcloud auth application-default login` on the Mac)."""

    vertex_project: str
    vertex_region: str = "global"

    @cached_property
    def _client(self) -> Any:
        return anthropic.AnthropicVertex(
            project_id=self.vertex_project,
            region=self.vertex_region,
            max_retries=self.max_retries,
            timeout=self.default_request_timeout,
        )

    @cached_property
    def _async_client(self) -> Any:
        return anthropic.AsyncAnthropicVertex(
            project_id=self.vertex_project,
            region=self.vertex_region,
            max_retries=self.max_retries,
            timeout=self.default_request_timeout,
        )


def ollama_url(config: ModelsConfig) -> str:
    return get_settings().ollama_url or config.providers["ollama"]["base_url"]


def claude_model(rc: RoleConfig, providers: dict[str, dict[str, str]]) -> ChatAnthropic:
    betas = [THINKING_BINDING_BETA]
    # Automatic prompt caching: repeated model calls within one turn re-read the system
    # prompt, tools and history at a twentieth of the input price.
    extra: dict[str, Any] = {"cache_control": {"type": "ephemeral"}}
    if rc.fallbacks:
        if rc.provider == "vertex":
            raise ValueError(
                "Server-side fallbacks aren't available on Vertex AI; set fallbacks: false"
            )
        betas.append(FALLBACK_BETA)
        extra["fallbacks"] = "default"
    # model_name / max_tokens_to_sample are ChatAnthropic's declared names for model / max_tokens.
    common: dict[str, Any] = {
        "model_name": rc.model,
        "max_tokens_to_sample": rc.max_tokens,
        "thinking": CLAUDE_THINKING,
        "output_config": {"effort": rc.effort} if rc.effort else None,
        "betas": betas,
        "model_kwargs": extra,
        # A chat reply that takes longer than this has stalled; the SDK default is 10 minutes.
        "timeout": 120.0,
        "stop": None,
    }
    settings = get_settings()
    if rc.provider == "vertex":
        if settings.gcp_project is None:
            raise ValueError(
                "MENTOR_GCP_PROJECT is not set (the Google Cloud project for Vertex AI)"
            )
        region = providers.get("vertex", {}).get("region", "global")
        return ChatClaudeOnVertex(
            vertex_project=settings.gcp_project, vertex_region=region, **common
        )
    if settings.anthropic_api_key is None:
        raise ValueError("MENTOR_ANTHROPIC_API_KEY is not set (see .env.example)")
    return ChatAnthropic(api_key=settings.anthropic_api_key, **common)


def get_chat_model(role: Role, config: ModelsConfig | None = None) -> BaseChatModel:
    config = config or load_models_config()
    if role not in config.roles:
        raise ValueError(f"No model configured for role {role!r} in config/models.yaml")
    rc = config.roles[role]
    if rc.provider == "ollama":
        return ChatOllama(
            model=rc.model,
            base_url=ollama_url(config),
            temperature=rc.temperature,
            reasoning=rc.reasoning,
            num_ctx=rc.num_ctx,
        )
    if rc.provider in CLAUDE_PROVIDERS:
        return claude_model(rc, config.providers)
    if rc.provider == "gemini":
        api_key = get_settings().gemini_api_key
        if api_key is None:
            raise ValueError("MENTOR_GEMINI_API_KEY is not set (see .env.example)")
        return ChatGoogleGenerativeAI(
            model=rc.model,
            api_key=api_key,
            temperature=rc.temperature,
            thinking_level=rc.thinking_level,
        )
    raise NotImplementedError("Bedrock models are wired up in step 0.4, once AWS access is ready")
