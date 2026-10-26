"""Runtime settings, read from MENTOR_* environment variables or the project's .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DIR = Path.home() / ".config" / "mentor"

Host = Literal["mac", "cloud"]


class Settings(BaseSettings):
    # Empty values in .env (e.g. `MENTOR_TELEGRAM_ALLOWED_CHAT_ID=`) count as not set.
    model_config = SettingsConfigDict(
        env_prefix="MENTOR_", env_file=REPO_ROOT / ".env", extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql+psycopg://mentor:mentor-local-only@127.0.0.1:5432/mentor"
    config_dir: Path = REPO_ROOT / "config"
    # Role-to-model map in config_dir: models.yaml (Ollama on the Mac) or models.cloud.yaml
    # (Gemini, on the Google Cloud server).
    models_file: str = "models.yaml"
    # Where this copy of Mentor runs, so it can say so when asked.
    host: Host = "mac"
    # The Google Cloud server the Mac's standby watches (same values as infra/gcp/config.sh).
    gcp_project: str | None = None
    gcp_zone: str = "us-west1-b"
    gcp_server: str = "mentor"
    # Overrides models.yaml's Ollama address, e.g. http://host.docker.internal:11434 when the bot
    # runs in a container on the Mac and Ollama runs on the Mac itself (for its GPU).
    ollama_url: str | None = None
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    # Web search and page reading (Tavily's free plan: 1,000 credits a month, no card).
    tavily_api_key: SecretStr | None = None
    # Optional: Semantic Scholar's free key makes its paper summaries (TLDRs) reliable.
    semantic_scholar_api_key: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_allowed_chat_id: int | None = None
    # Outside the repo, owner-only permissions; Secrets Manager from Phase 1.
    google_client_secret_path: Path = PRIVATE_DIR / "google_client_secret.json"
    google_token_path: Path = PRIVATE_DIR / "google_token.json"
    # LinkedIn, through Mentor's read-only gateway on the Mac (`mentor linkedin`), which makes its
    # own token on first start. The bot's container reaches it at host.docker.internal.
    linkedin_gateway_url: str = "http://127.0.0.1:8765/mcp"
    linkedin_token_path: Path = PRIVATE_DIR / "linkedin_gateway_token"


@lru_cache
def get_settings() -> Settings:
    return Settings()
