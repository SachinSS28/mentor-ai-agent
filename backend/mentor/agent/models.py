"""Model router: maps each role to a model from config/models.yaml (or MENTOR_MODELS_FILE)."""

from pathlib import Path
from typing import Literal

import yaml
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from pydantic import BaseModel

from mentor.settings import get_settings

Role = Literal["orchestrator", "fast", "bulk", "embed"]


class RoleConfig(BaseModel):
    provider: Literal["ollama", "gemini", "bedrock"]
    model: str
    # None sends no temperature: Gemini 3.6+ models reject custom sampling settings.
    temperature: float | None = 0.3
    # Ollama only. None keeps the model's default. Gemma 4 returns empty answers after tool
    # calls when thinking is switched off, so don't set false for tool-using roles.
    reasoning: bool | None = None
    # Gemini only: how hard the model thinks before answering ("low" is quick and cheap).
    thinking_level: Literal["minimal", "low", "medium", "high"] | None = None


class ModelsConfig(BaseModel):
    providers: dict[str, dict[str, str]]
    roles: dict[Role, RoleConfig]


def load_models_config(path: Path | None = None) -> ModelsConfig:
    settings = get_settings()
    path = path or settings.config_dir / settings.models_file
    return ModelsConfig.model_validate(yaml.safe_load(path.read_text()))


def get_chat_model(role: Role, config: ModelsConfig | None = None) -> BaseChatModel:
    config = config or load_models_config()
    if role not in config.roles:
        raise ValueError(f"No model configured for role {role!r} in config/models.yaml")
    rc = config.roles[role]
    if rc.provider == "ollama":
        return ChatOllama(
            model=rc.model,
            base_url=config.providers["ollama"]["base_url"],
            temperature=rc.temperature,
            reasoning=rc.reasoning,
        )
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
