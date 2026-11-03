"""Mentor's Google outbox: messages that reach the owner on Telegram even while the Mac is off.

Telegram bots can't schedule messages, and Mentor runs on the Mac, so a small Google Apps Script
in the owner's own Google account (infra/outbox/Code.gs) holds Mentor's plan and sends each
message at its time; Google runs it every minute. While the Mac is on, Mentor keeps the plan up
to date (every minute when reminders change, every 15 minutes anyway):

- task reminders for the next week, sent by the outbox at their time (the bot then leaves them
  alone, so none comes twice; if the outbox can't be reached, the bot sends them as before);
- a morning plan for 20 minutes after the morning message, sent only if the Mac missed the
  morning message that day (it's taken out of the plan once the live message has gone).

Mentor writes these while the Mac is on; it can't think or reply while the Mac is off.
"""

import json
import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from secrets import token_urlsafe
from typing import Any

import httpx

from mentor.channels.telegram.formatting import to_telegram_html
from mentor.charter import get_charter, owner_timezone
from mentor.db.models import Task
from mentor.db.state import get_state, set_state
from mentor.jobs.desk import DIGEST_SENT_ON
from mentor.mcp_servers.google_tools import calendar
from mentor.mcp_servers.mentor_core import tasks
from mentor.practice.dhamma import uposatha
from mentor.settings import get_settings

log = logging.getLogger(__name__)

SYNCED = "outbox.synced"  # the plan the outbox last accepted: {"at": ..., "messages": {id: text}}
RECORDED = "outbox.recorded"  # delivered messages already added to the conversation
REMINDER_DAYS = 7
MORNING_DELAY = timedelta(minutes=20)  # the morning plan waits this long for the live message
GIVE_UP = timedelta(minutes=30)  # a reminder the outbox hasn't confirmed by then: the bot sends it
WEB_APP = re.compile(r"^https://script\.google\.com/macros/s/[\w-]+/exec$")
TASK_ID = re.compile(r"^task-(\d+)-(\d+)$")
TIMEOUT = 30


@dataclass(frozen=True)
class Planned:
    id: str
    at: datetime
    text: str  # Markdown, as Mentor writes; sent to Telegram as its HTML

    def payload(self) -> dict[str, Any]:
        return {"id": self.id, "at": int(self.at.timestamp() * 1000), "text": self.html}

    @property
    def html(self) -> str:
        return to_telegram_html(self.text)


class OutboxError(Exception):
    """The outbox couldn't be reached or refused; the message says why."""


def config() -> dict[str, str] | None:
    """The outbox's address and Mentor's secret for it, or None if it isn't set up."""
    try:
        found = json.loads(get_settings().outbox_path.read_text())
    except (OSError, ValueError):
        return None
    return found if found.get("url") and found.get("secret") else None


def post(cfg: dict[str, str], action: str, **fields: Any) -> dict[str, Any]:
    """One request to the outbox (Google answers a script's POST through a redirect)."""
    body = {"action": action, "secret": cfg["secret"], **fields}
    try:
        response = httpx.post(cfg["url"], json=body, follow_redirects=True, timeout=TIMEOUT)
        response.raise_for_status()
        answer = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise OutboxError(f"the outbox can't be reached ({type(error).__name__})") from None
    if not isinstance(answer, dict) or not answer.get("ok"):
        message = answer.get("error") if isinstance(answer, dict) else None
        raise OutboxError(f"the outbox refused: {message or 'no reason given'}")
    return answer


def connect(url: str) -> None:
    """Connects a newly deployed outbox: sends it the bot's key, the chat and a new secret."""
    url = url.strip()
    if not WEB_APP.match(url):
        raise OutboxError("that isn't a script's web app address (…/macros/s/…/exec)")
    settings = get_settings()
    if settings.telegram_bot_token is None or settings.telegram_allowed_chat_id is None:
        raise OutboxError("the bot's key and chat must be in .env first")
    cfg = {"url": url, "secret": token_urlsafe(32)}
    post(
        cfg,
        "connect",
        token=settings.telegram_bot_token.get_secret_value(),
        chat_id=settings.telegram_allowed_chat_id,
    )
    path = settings.outbox_path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump(cfg, file)


def reminder_id(task: Task) -> str:
    when = task.remind_at.timestamp() if task.remind_at else 0
    return f"task-{task.ref}-{int(when)}"


def morning_at(day: date) -> datetime:
    at = time.fromisoformat(get_charter().job_search.digest_time)
    return datetime.combine(day, at, owner_timezone()) + MORNING_DELAY


def morning_plan(day: date, now: datetime) -> str:
    """The day at a glance, from what Mentor knows now, for a morning the Mac misses."""
    owner = get_charter().owner.name
    tz = owner_timezone()
    start = datetime.combine(day, time.min, tz)
    lines = [f"☀️ **Good morning, {owner}: your plan for {day:%a %d %b}**"]
    try:
        events = calendar.list_events(day, 1, tz)
        if events:
            lines += ["", "**Calendar**"] + [f"• {calendar.format_event(e)}" for e in events[:8]]
    except Exception:
        log.warning("The morning plan goes without the calendar")
    due = [t for t in tasks.due_soon(start, days=1) if t.due and t.due.date() == day]
    if due:
        lines += ["", "**Due today**"] + [
            f"• T{t.ref} {t.title} ({t.due.astimezone(tz):%H:%M})" for t in due[:8] if t.due
        ]
    late = tasks.overdue(start)
    if late:
        lines += ["", "**Overdue**"] + [f"• T{t.ref} {t.title}" for t in late[:6]]
    reminders = tasks.upcoming_reminders(start, start + timedelta(days=1))
    if reminders:
        lines += ["", "**Reminders today**"] + [
            f"• {t.remind_at.astimezone(tz):%H:%M} T{t.ref} {t.title}"
            for t in reminders[:8]
            if t.remind_at
        ]
    if phase := uposatha(day, now):
        lines += ["", f"🌕 Uposatha today ({phase}): a good day for the eight precepts."]
    if len(lines) == 1:
        lines.append("Nothing due and no meetings on record: a good day for deep work.")
    lines += [
        "",
        f"*Prepared on {now:%a %d %b at %H:%M} while your Mac was on; I'll answer messages "
        "once it's back.*",
    ]
    return "\n".join(lines)


def plan(now: datetime) -> list[Planned]:
    """Everything the outbox should hold: the week's reminders and the morning plans."""
    planned = [
        Planned(reminder_id(t), t.remind_at, tasks.reminder_text(t))
        for t in tasks.upcoming_reminders(now, now + timedelta(days=REMINDER_DAYS))
        if t.remind_at is not None
    ]
    sent_today = get_state(DIGEST_SENT_ON) == now.date().isoformat()
    for day in (now.date(), now.date() + timedelta(days=1)):
        at = morning_at(day)
        if at > now and not (day == now.date() and sent_today):
            planned.append(Planned(f"morning-{day.isoformat()}", at, morning_plan(day, now)))
    return planned


def synced() -> dict[str, Any]:
    found = json.loads(get_state(SYNCED) or "{}")
    return found if isinstance(found, dict) else {}


def handed_over() -> set[str]:
    """The messages the outbox holds or has sent, as of the last plan it accepted."""
    return set(synced().get("messages", {}))


def changed(now: datetime) -> bool:
    """Whether the reminders differ from what the outbox holds (then it's worth a sync now)."""
    coming = tasks.upcoming_reminders(now, now + timedelta(days=REMINDER_DAYS))
    ids = {reminder_id(t) for t in coming}
    held = {i for i in handed_over() if i.startswith("task-")}
    return ids != {i for i in held if int(i.rsplit("-", 1)[1]) > now.timestamp()}


def sync(
    now: datetime, extra: Sequence[Planned] = (), on_delivered: Callable[[str], None] | None = None
) -> bool:
    """Hands the outbox the current plan. Delivered reminders are marked as reminded, and each
    newly delivered message is passed to on_delivered (to add it to the conversation)."""
    cfg = config()
    if cfg is None:
        return False
    messages = [*plan(now), *extra]
    try:
        answer = post(cfg, "plan", messages=[m.payload() for m in messages])
    except OutboxError as error:
        log.warning("Couldn't update the outbox: %s", error)
        return False
    before = synced().get("messages", {})
    sent = [str(i) for i in answer.get("sent") or []]
    record_delivered(sent, before, now, on_delivered)
    held = {m.id: m.text for m in messages} | {i: t for i, t in before.items() if i in sent}
    set_state(SYNCED, json.dumps({"at": now.isoformat(), "messages": held}))
    return True


def record_delivered(
    sent: Sequence[str],
    texts: dict[str, str],
    now: datetime,
    on_delivered: Callable[[str], None] | None,
) -> None:
    recorded = json.loads(get_state(RECORDED) or "[]")
    new = [i for i in sent if i not in recorded]
    refs = [int(m.group(1)) for i in new if (m := TASK_ID.match(i))]
    if refs:
        tasks.mark_reminded(refs, now)
    for message_id in new:
        if on_delivered and message_id in texts:
            on_delivered(texts[message_id])
    if new:
        set_state(RECORDED, json.dumps([*recorded, *new][-300:]))


def bot_sends(task: Task, now: datetime) -> bool:
    """Whether the bot itself should send this due reminder: the outbox doesn't hold it, or it
    hasn't confirmed sending it long after its time."""
    if config() is None or reminder_id(task) not in handed_over():
        return True
    return task.remind_at is not None and now - task.remind_at > GIVE_UP


def test_message(now: datetime) -> Planned:
    """A message for the next minute, to check the outbox delivers."""
    return Planned(
        f"test-{int(now.timestamp())}",
        now + timedelta(minutes=1),
        "✅ **Mentor's Google outbox works.** Reminders and your morning plan will reach you "
        "here even while the Mac is off.",
    )
