"""Runtime settings, read from MENTOR_* environment variables or the project's .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Empty values in .env (e.g. `MENTOR_TELEGRAM_ALLOWED_CHAT_ID=`) count as not set.
    model_config = SettingsConfigDict(
        env_prefix="MENTOR_", env_file=REPO_ROOT / ".env", extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql+psycopg://mentor:mentor-local-only@127.0.0.1:5432/mentor"
    config_dir: Path = REPO_ROOT / "config"
    telegram_bot_token: SecretStr | None = None
    telegram_allowed_chat_id: int | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
