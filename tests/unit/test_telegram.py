import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import Update
from telegram.error import BadRequest, NetworkError

from mentor.channels.telegram.bot import (
    decide,
    make_error_handler,
    pairing_reply,
    quota_reset_time,
    send_reply,
    split_message,
)
from mentor.channels.telegram.formatting import to_telegram_html


def message_from(chat_id: int) -> Mock:
    update = Mock(spec=Update)
    update.effective_chat.id = chat_id
    update.effective_message.reply_text = AsyncMock()
    return update


def handle_error(update: object, error: Exception, allowed_chat_id: int = 42) -> None:
    context: Any = SimpleNamespace(error=error)
    asyncio.run(make_error_handler(allowed_chat_id)(update, context))


def test_answers_only_the_allowed_chat() -> None:
    assert decide(42, allowed_chat_id=42) == "answer"
    assert decide(99, allowed_chat_id=42) == "ignore"


def test_pairs_before_an_allowed_chat_is_set() -> None:
    assert decide(42, allowed_chat_id=None) == "pair"
    assert "MENTOR_TELEGRAM_ALLOWED_CHAT_ID=42" in pairing_reply(42)


def test_long_replies_are_split_under_telegrams_limit() -> None:
    parts = split_message("x" * 9000)
    assert [len(p) for p in parts] == [4096, 4096, 808]
    assert split_message("") == [""]


def test_lost_connection_is_one_log_line(caplog: pytest.LogCaptureFixture) -> None:
    error = NetworkError("httpx.ConnectError: [Errno 8] nodename nor servname provided")
    with caplog.at_level(logging.WARNING):
        handle_error(None, error)
    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert "nodename nor servname" in record.getMessage()
    assert record.exc_info is None


def test_owner_hears_when_a_message_fails(caplog: pytest.LogCaptureFixture) -> None:
    update = message_from(42)
    with caplog.at_level(logging.ERROR):
        handle_error(update, ConnectionError("Ollama is down"))
    update.effective_message.reply_text.assert_awaited_once()
    assert "ConnectionError" in update.effective_message.reply_text.await_args.args[0]
    assert caplog.records[0].exc_info is not None


def test_owner_hears_when_gemini_usage_limit_is_hit() -> None:
    update = message_from(42)
    handle_error(update, RuntimeError("429 RESOURCE_EXHAUSTED. Quota exceeded for metric ..."))
    assert "Gemini's usage limit" in update.effective_message.reply_text.await_args.args[0]


def test_gemini_quota_resets_at_pacific_midnight_in_the_owners_time() -> None:
    # The charter's owner is in Hong Kong: 15:00 while the US is on daylight time, else 16:00.
    assert quota_reset_time(datetime(2026, 10, 17, 12, 0, tzinfo=UTC)) == "15:00"
    assert quota_reset_time(datetime(2026, 12, 1, 12, 0, tzinfo=UTC)) == "16:00"


def test_markdown_is_shown_as_telegram_formatting() -> None:
    reply = (
        "* **Retreat — Hills** (28 Jul – 24 Oct)\n## Next\nRun `ls -a` & see [docs](https://x.io)"
    )
    assert to_telegram_html(reply) == (
        "• <b>Retreat — Hills</b> (28 Jul – 24 Oct)\n<b>Next</b>\n"
        'Run <code>ls -a</code> &amp; see <a href="https://x.io">docs</a>'
    )


def test_formatting_leaves_code_and_stray_symbols_alone() -> None:
    reply = "2 * 3 * 4 and snake_case\n```\nif a < b: print('**x**')\n```"
    assert to_telegram_html(reply) == (
        "2 * 3 * 4 and snake_case\n<pre>if a &lt; b: print('**x**')</pre>"
    )


def test_reply_falls_back_to_plain_text_when_telegram_refuses_the_formatting() -> None:
    message = Mock()
    message.reply_text = AsyncMock(side_effect=[BadRequest("Can't parse entities"), None])
    asyncio.run(send_reply(message, "**hi**"))
    first, second = message.reply_text.await_args_list
    assert first.args == ("<b>hi</b>",) and first.kwargs == {"parse_mode": "HTML"}
    assert second.args == ("**hi**",) and second.kwargs == {}


def test_failures_are_not_reported_to_other_chats() -> None:
    update = message_from(99)
    handle_error(update, RuntimeError("boom"))
    update.effective_message.reply_text.assert_not_awaited()
