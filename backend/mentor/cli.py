"""`mentor` command: chat in the terminal or run the Telegram bot."""

import argparse
import os
from collections.abc import Sequence

import httpx
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from mentor.agent.models import load_models_config, ollama_url
from mentor.db.session import get_engine
from mentor.settings import get_settings


def not_ready() -> str | None:
    """Why Mentor can't run here right now (database or model server down), with the fix."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        return (
            "Can't reach the database. Start it with:\n"
            "  docker compose -f infra/docker-compose.yml up -d && uv run alembic upgrade head"
        )

    config = load_models_config()
    providers = {role.provider for role in config.roles.values()}
    if "gemini" in providers and get_settings().gemini_api_key is None:
        return "MENTOR_GEMINI_API_KEY is not set (see .env.example)."
    if "anthropic" in providers and get_settings().anthropic_api_key is None:
        return "MENTOR_ANTHROPIC_API_KEY is not set (see .env.example)."
    if "vertex" in providers and get_settings().gcp_project is None:
        return "MENTOR_GCP_PROJECT is not set (the Google Cloud project for Vertex AI)."
    if "ollama" in providers:
        base_url = ollama_url(config)
        try:
            httpx.get(f"{base_url}/api/version", timeout=3).raise_for_status()
        except httpx.HTTPError:
            return f"Can't reach Ollama at {base_url}. Start it with: ollama serve"
    return None


def preflight() -> None:
    """Fail fast with a clear fix if the database or the local model server is down."""
    problem = not_ready()
    if problem:
        raise SystemExit(problem)


def run_outbox(url: str | None, test: bool, setup: bool = False) -> None:
    from datetime import datetime

    from mentor import outbox
    from mentor.charter import owner_timezone

    try:
        if setup:
            from mentor import outbox_setup

            outbox_setup.setup()
        elif url:
            outbox.connect(url)
        connected = setup or bool(url)
        if connected:
            print("Connected: the outbox has the bot's key and Mentor's secret.", flush=True)
        cfg = outbox.config()
        if cfg is None:
            raise SystemExit("No outbox yet: run `mentor outbox --setup`.")
        now = datetime.now(owner_timezone())
        extra = [outbox.test_message(now)] if connected or test else []
        if not outbox.sync(now, extra):
            raise SystemExit("The outbox didn't take the plan; see the log above.")
        status = outbox.post(cfg, "status")
    except outbox.OutboxError as error:
        raise SystemExit(f"Outbox: {error}") from None
    print(f"The outbox holds {status.get('pending', 0)} message(s).")
    if extra:
        print("A test message reaches Telegram in about a minute.")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mentor", description="Mentor: your AI chief of staff")
    commands = parser.add_subparsers(dest="command", required=True)
    chat = commands.add_parser("chat", help="chat with Mentor in the terminal")
    chat.add_argument("--new", action="store_true", help="start a fresh conversation")
    chat.add_argument("--once", metavar="TEXT", help="ask one question, print the reply, exit")
    commands.add_parser("telegram", help="run the Telegram bot")
    commands.add_parser(
        "local", help="run the chat page on this Mac (http://localhost:8790), works offline"
    )
    commands.add_parser(
        "standby", help="on the Mac: take over Telegram while the cloud server is down"
    )
    commands.add_parser(
        "sync", help="copy conversations and memories between the Mac and the cloud server"
    )
    commands.add_parser(
        "google-auth", help="connect your Google account (one-time, in the browser)"
    )
    commands.add_parser(
        "watch", help="on the Mac: keep the activity journal (what's in front, once a minute)"
    )
    commands.add_parser(
        "awake", help="on the Mac: keep it from sleeping while on power, so Mentor keeps running"
    )
    outbox = commands.add_parser(
        "outbox", help="the Google outbox: messages that arrive even while the Mac is off"
    )
    outbox.add_argument(
        "--setup", action="store_true", help="create the outbox in your Google account (one time)"
    )
    outbox.add_argument("--connect", metavar="URL", help="connect a newly deployed outbox")
    outbox.add_argument("--test", action="store_true", help="send a test message in a minute")
    linkedin = commands.add_parser(
        "linkedin", help="on the Mac: run the read-only LinkedIn gateway for the bot"
    )
    linkedin.add_argument(
        "--login", action="store_true", help="sign in to LinkedIn once, in its own browser window"
    )
    linkedin.add_argument("--safari", action="store_true", help="open LinkedIn in Safari browser")
    args = parser.parse_args(argv)

    if args.command in ("standby", "sync"):
        from mentor.channels.telegram import failover

        failover.run_standby() if args.command == "standby" else failover.sync_now()
        return

    if args.command == "outbox":
        run_outbox(args.connect, args.test, args.setup)
        return

    if args.command == "awake":
        # caffeinate -s holds the Mac awake only on power; the display still sleeps.
        os.execv("/usr/bin/caffeinate", ["caffeinate", "-s"])  # noqa: S606 (fixed command)

    if args.command == "watch":
        from mentor.activity import watch

        watch()
        return

    if args.command == "linkedin":
        from mentor.linkedin import gateway

        if args.login:
            gateway.login()
        elif args.safari:
            import subprocess

            subprocess.run(
                ["/usr/bin/open", "-a", "Safari", "https://www.linkedin.com/feed/"], check=False
            )
        else:
            gateway.serve()
        return

    if args.command == "google-auth":
        from mentor.mcp_servers.google_tools.auth import GoogleNotConnectedError, connect_google

        try:
            print(f"Google connected. Scopes: {connect_google()}")
        except GoogleNotConnectedError as error:
            raise SystemExit(str(error)) from None
        return

    preflight()
    if args.command == "local":
        from mentor.channels.local import serve

        serve()
        return
    if args.command == "chat":
        from mentor.channels.terminal import run_terminal_chat

        run_terminal_chat(new_conversation=args.new, once=args.once)
    else:
        from mentor.channels.telegram.bot import run_telegram_bot

        run_telegram_bot()
