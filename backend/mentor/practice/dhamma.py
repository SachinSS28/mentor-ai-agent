"""Daily Dhamma: the morning teaching, the evening reflection, and Uposatha reminders.

Teachings and prompts come from config/dhamma.yaml (paraphrases with their Pali Canon sources),
never from the model, so nothing is misquoted or misattributed.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel

from mentor.charter import get_charter
from mentor.db.state import get_state, set_state
from mentor.jobs.desk import digest_due
from mentor.practice.moon import uposatha_on
from mentor.settings import get_settings

EVENING_SENT_ON = "evening.sent_on"
UPOSATHA_PRACTICE = (
    "a good day to keep the eight precepts, sit longer in meditation, or visit a monastery "
    "to hear the Dhamma."
)


class Teaching(BaseModel):
    theme: str
    source: str
    teaching: str
    practice: str


class DhammaConfig(BaseModel):
    teachings: list[Teaching] = []
    evening_prompts: list[str] = []


def load(path: Path | None = None) -> DhammaConfig:
    path = path or get_settings().config_dir / "dhamma.yaml"
    if not path.exists():
        return DhammaConfig()
    return DhammaConfig.model_validate(yaml.safe_load(path.read_text()) or {})


def pick[T](items: Sequence[T], day: date) -> T:
    """The same item all day, a different one each day, cycling through the list."""
    return items[day.toordinal() % len(items)]


def uposatha(day: date, now: datetime) -> str | None:
    if now.tzinfo is None:
        raise ValueError("now must carry the owner's time zone")
    phase = uposatha_on(day, now.tzinfo)
    return phase if phase and phase in get_charter().practice.uposatha else None


def morning_section(now: datetime) -> str | None:
    config = load()
    if not get_charter().practice.morning_dhamma or not config.teachings:
        return None
    teaching = pick(config.teachings, now.date())
    lines = [
        f"**Dhamma for today: {teaching.theme}**",
        f"{teaching.teaching} ({teaching.source}, paraphrased)",
        f"Practice: {teaching.practice}",
    ]
    if phase := uposatha(now.date(), now):
        lines.append(f"Today is Uposatha ({phase}): {UPOSATHA_PRACTICE}")
    return "\n".join(lines)


def evening_message(now: datetime) -> str:
    config = load()
    lines = ["**Evening reflection**"]
    if config.evening_prompts:
        lines.append(pick(config.evening_prompts, now.date()))
    if phase := uposatha(now.date() + timedelta(days=1), now):
        lines.append(f"Tomorrow is Uposatha ({phase}): {UPOSATHA_PRACTICE}")
    lines.append("Reply with your reflection if you like; it stays between us.")
    return "\n".join(lines)


def evening_tick(now: datetime) -> str | None:
    """The evening reflection once a day at the charter's evening_time (or the first check
    after it the same day), else None."""
    practice = get_charter().practice
    if not practice.evening_reflection:
        return None
    if not digest_due(now, get_state(EVENING_SENT_ON), practice.evening_time):
        return None
    set_state(EVENING_SENT_ON, now.date().isoformat())
    return evening_message(now)
