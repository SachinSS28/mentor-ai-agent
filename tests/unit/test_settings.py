from pathlib import Path

from mentor.settings import Settings


def test_empty_values_in_env_file_count_as_not_set(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("MENTOR_TELEGRAM_BOT_TOKEN=\nMENTOR_TELEGRAM_ALLOWED_CHAT_ID=\n")
    settings = Settings(_env_file=env)  # type: ignore[call-arg]
    assert settings.telegram_bot_token is None
    assert settings.telegram_allowed_chat_id is None


def test_allowed_chat_id_is_read_as_a_number(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("MENTOR_TELEGRAM_ALLOWED_CHAT_ID=123456789\n")
    assert Settings(_env_file=env).telegram_allowed_chat_id == 123456789  # type: ignore[call-arg]
