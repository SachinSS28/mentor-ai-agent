"""Telegram bot (long polling, so no public endpoint is needed).

Mentor answers only the one chat ID in MENTOR_TELEGRAM_ALLOWED_CHAT_ID. Until that is set,
it replies to private messages with the sender's chat ID so you can add yours, and nothing else.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy.exc import SQLAlchemyError
from telegram import Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, NetworkError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from mentor.agent.app import open_agent
from mentor.agent.graph import MentorGraph, ask
from mentor.channels.telegram.formatting import to_telegram_html
from mentor.charter import owner_timezone
from mentor.db.heartbeat import beat
from mentor.db.session import get_engine
from mentor.settings import Host, get_settings

log = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
# The Mac's standby treats a few minutes without one as the cloud bot being down.
HEARTBEAT_EVERY = 30

Decision = Literal["answer", "pair", "ignore"]
ErrorHandler = Callable[[object, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]


def decide(chat_id: int, allowed_chat_id: int | None) -> Decision:
    if allowed_chat_id is None:
        return "pair"
    return "answer" if chat_id == allowed_chat_id else "ignore"


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)] or [""]


def pairing_reply(chat_id: int) -> str:
    return (
        f"Your chat ID is {chat_id}. Add MENTOR_TELEGRAM_ALLOWED_CHAT_ID={chat_id} to the .env "
        "file in the project and restart Mentor. Until then I won't answer anything."
    )


async def send_reply(message: Message, reply: str) -> None:
    # Split before converting, so no part ends inside a tag. Telegram counts the limit
    # after removing tags, so each converted part still fits.
    for part in split_message(reply):
        try:
            await message.reply_text(to_telegram_html(part), parse_mode=ParseMode.HTML)
        except BadRequest as error:
            log.warning("Telegram refused the formatting (%s); sending plain text", error)
            await message.reply_text(part)


def make_handler(
    graph: MentorGraph, allowed_chat_id: int | None
) -> MessageHandler[ContextTypes.DEFAULT_TYPE, None]:
    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat, message = update.effective_chat, update.effective_message
        if chat is None or message is None or message.text is None:
            return
        decision = decide(chat.id, allowed_chat_id)
        if decision == "pair":
            await message.reply_text(pairing_reply(chat.id))
        elif decision == "answer":
            await chat.send_action(ChatAction.TYPING)
            reply = await asyncio.to_thread(ask, graph, message.text, f"telegram-{chat.id}")
            await send_reply(message, reply)

    return MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, on_text)


def quota_reset_time(now: datetime) -> str:
    """When Gemini's daily quota resets (midnight US Pacific time), in the owner's time zone."""
    pacific = now.astimezone(ZoneInfo("America/Los_Angeles"))
    midnight = datetime.combine(pacific.date() + timedelta(days=1), time(), pacific.tzinfo)
    return f"{midnight.astimezone(owner_timezone()):%H:%M}"


def claude_error_reply(error: BaseException) -> str | None:
    """What to tell the owner when the Claude API fails in a way they can act on."""
    if isinstance(error, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return (
            "Claude refused my sign-in. On Google Cloud, check that Claude is enabled in Vertex "
            "AI Model Garden and that the server's service account has the Vertex AI User role."
        )
    if isinstance(error, anthropic.NotFoundError):
        return (
            "Claude's model isn't available to me. Check that it is enabled in Vertex AI Model "
            "Garden for this project."
        )
    if isinstance(error, anthropic.RateLimitError):
        return "I've hit Claude's rate limit. Please try again in a minute."
    if isinstance(error, anthropic.BadRequestError):
        detail = str(error).lower()
        if "credit balance" in detail:
            return (
                "My Claude API credits have run out. Top up at platform.claude.com "
                "(Settings, Billing), then try again."
            )
        if "usage limit" in detail:
            return (
                "This month's Claude spending limit is reached. Raise it at platform.claude.com "
                "(Settings, Limits) or wait for the next month."
            )
    if isinstance(error, anthropic.APIStatusError) and error.status_code >= 500:
        return "Claude is overloaded right now. Please try again in a few minutes."
    if isinstance(error, anthropic.APIConnectionError):
        return "I couldn't reach Claude's servers. Please try again in a minute."
    return None


def error_reply(error: BaseException | None) -> str:
    if error and (claude := claude_error_reply(error)):
        return claude
    # Gemini's free tier caps requests per minute and per day.
    if error and "RESOURCE_EXHAUSTED" in str(error):
        return (
            "I've hit Gemini's usage limit. Try again in a minute; if it keeps happening, "
            f"today's free quota is used up and resets at {quota_reset_time(datetime.now(UTC))}."
        )
    reason = type(error).__name__ if error else "unknown error"
    return f"Sorry, something went wrong on my side ({reason}). Please try again in a minute."


def make_error_handler(allowed_chat_id: int | None) -> ErrorHandler:
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        error = context.error
        if isinstance(error, NetworkError):
            # No internet, or the Mac just woke from sleep. The bot retries by itself (backing
            # off to every 30 seconds), so one line per attempt is enough.
            log.warning("Can't reach Telegram, retrying: %s", error)
            return
        log.error("Couldn't handle an update", exc_info=error)
        # Tell the owner instead of going quiet, e.g. when Ollama or the database is down.
        if not isinstance(update, Update):
            return
        chat, message = update.effective_chat, update.effective_message
        if chat and message and decide(chat.id, allowed_chat_id) == "answer":
            await message.reply_text(error_reply(error))

    return on_error


def configure_logging() -> None:
    """Timestamped one-line log entries; launchd sends them to ~/Library/Logs/Mentor/."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S%z",
    )
    # httpx logs every request at INFO, and Telegram request URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def keep_beating(host: Host) -> None:
    """Record every HEARTBEAT_EVERY seconds that this bot is alive (see failover.py)."""
    while True:
        try:
            await asyncio.to_thread(beat, get_engine(), host)
        except SQLAlchemyError as error:
            log.warning("Couldn't record the heartbeat: %s", error)
        await asyncio.sleep(HEARTBEAT_EVERY)


def run_telegram_bot() -> None:
    configure_logging()
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("MENTOR_TELEGRAM_BOT_TOKEN is not set in .env (see .env.example).")
    status = settings.telegram_allowed_chat_id or "not set yet (pairing mode)"

    async def announce(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        log.info(
            "Mentor is listening on Telegram as @%s. Allowed chat: %s. Ctrl+C stops it.",
            app.bot.username,
            status,
        )
        app.bot_data["heartbeat"] = asyncio.create_task(keep_beating(settings.host))

    async def stop_beating(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        app.bot_data["heartbeat"].cancel()

    with open_agent() as graph:
        token = settings.telegram_bot_token.get_secret_value()
        builder = Application.builder().token(token).post_init(announce).post_stop(stop_beating)
        app = builder.build()
        app.add_handler(make_handler(graph, settings.telegram_allowed_chat_id))
        app.add_error_handler(make_error_handler(settings.telegram_allowed_chat_id))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
