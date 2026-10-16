"""Telegram bot (long polling, so no public endpoint is needed).

Mentor answers only the one chat ID in MENTOR_TELEGRAM_ALLOWED_CHAT_ID. Until that is set,
it replies to private messages with the sender's chat ID so you can add yours, and nothing else.
"""

import asyncio
from typing import Any, Literal

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from mentor.agent.app import open_agent
from mentor.agent.graph import MentorGraph, ask
from mentor.settings import get_settings

TELEGRAM_MESSAGE_LIMIT = 4096

Decision = Literal["answer", "pair", "ignore"]


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
            for part in split_message(reply):
                await message.reply_text(part)

    return MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, on_text)


def run_telegram_bot() -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("MENTOR_TELEGRAM_BOT_TOKEN is not set in .env (see .env.example).")
    status = settings.telegram_allowed_chat_id or "not set yet (pairing mode)"

    async def announce(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        print(
            f"Mentor is listening on Telegram as @{app.bot.username}. "
            f"Allowed chat: {status}. Ctrl+C stops it.",
            flush=True,
        )

    with open_agent() as graph:
        token = settings.telegram_bot_token.get_secret_value()
        app = Application.builder().token(token).post_init(announce).build()
        app.add_handler(make_handler(graph, settings.telegram_allowed_chat_id))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
