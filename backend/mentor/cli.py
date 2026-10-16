"""`mentor` command: chat in the terminal or run the Telegram bot."""

import argparse
from collections.abc import Sequence

import httpx
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from mentor.agent.models import load_models_config
from mentor.db.session import get_engine


def preflight() -> None:
    """Fail fast with a clear fix if the database or the local model server is down."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        raise SystemExit(
            "Can't reach the database. Start it with:\n"
            "  docker compose -f infra/docker-compose.yml up -d && uv run alembic upgrade head"
        ) from None

    config = load_models_config()
    if any(role.provider == "ollama" for role in config.roles.values()):
        base_url = config.providers["ollama"]["base_url"]
        try:
            httpx.get(f"{base_url}/api/version", timeout=3).raise_for_status()
        except httpx.HTTPError:
            raise SystemExit(
                f"Can't reach Ollama at {base_url}. Start it with: ollama serve"
            ) from None


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mentor", description="Mentor: your AI chief of staff")
    commands = parser.add_subparsers(dest="command", required=True)
    chat = commands.add_parser("chat", help="chat with Mentor in the terminal")
    chat.add_argument("--new", action="store_true", help="start a fresh conversation")
    chat.add_argument("--once", metavar="TEXT", help="ask one question, print the reply, exit")
    commands.add_parser("telegram", help="run the Telegram bot")
    commands.add_parser(
        "google-auth", help="connect your Google account (one-time, in the browser)"
    )
    args = parser.parse_args(argv)

    if args.command == "google-auth":
        from mentor.mcp_servers.google_tools.auth import GoogleNotConnectedError, connect_google

        try:
            print(f"Google connected. Scopes: {connect_google()}")
        except GoogleNotConnectedError as error:
            raise SystemExit(str(error)) from None
        return

    preflight()
    if args.command == "chat":
        from mentor.channels.terminal import run_terminal_chat

        run_terminal_chat(new_conversation=args.new, once=args.once)
    else:
        from mentor.channels.telegram.bot import run_telegram_bot

        run_telegram_bot()
