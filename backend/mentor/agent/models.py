"""Model router: maps each role to a model from config/models.yaml."""

from pathlib import Path
from typing import Literal

import yaml
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from pydantic import BaseModel

from mentor.settings import get_settings

Role = Literal["orchestrator", "fast", "bulk", "embed"]


class RoleConfig(BaseModel):
    provider: Literal["ollama", "bedrock"]
    model: str
    temperature: float = 0.3
    # None keeps the model's default. Gemma 4 returns empty answers after tool calls
    # when thinking is switched off, so don't set false for tool-using roles.
    reasoning: bool | None = None


class ModelsConfig(BaseModel):
    providers: dict[str, dict[str, str]]
    roles: dict[Role, RoleConfig]


def load_models_config(path: Path | None = None) -> ModelsConfig:
    path = path or get_settings().config_dir / "models.yaml"
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
    raise NotImplementedError("Bedrock models are wired up in step 0.4, once AWS access is ready")
