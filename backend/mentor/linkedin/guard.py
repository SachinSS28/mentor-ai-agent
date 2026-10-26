"""The LinkedIn guard. Every LinkedIn look-up passes here, inside the gateway on the Mac:

- only LinkedIn's read tools (never connect, message or the inbox);
- a small daily and hourly budget, and a pause of 20-40 seconds between look-ups;
- waking hours only;
- a stop whenever LinkedIn pushes back: a rate limit pauses look-ups for a day; a login wall,
  security check or account restriction stops them until the owner has dealt with it on
  linkedin.com and sends /linkedin on.

State lives in the `state` table, so the bot's /linkedin command and the gateway agree.
"""

import json
import random
from collections.abc import Callable, Mapping
from datetime import datetime, time, timedelta
from typing import Any

from mentor.charter import LinkedInConfig
from mentor.db.state import get_state, set_state

READ_TOOLS = frozenset(
    {
        "search_jobs",
        "get_job_details",
        "search_people",
        "get_person_profile",
        "get_company_profile",
        "get_company_posts",
        "search_posts",
    }
)
RECENT = "linkedin.recent"  # look-up times in the last day, as a JSON list
NEXT = "linkedin.next"  # the earliest time for the next look-up
STOP = "linkedin.stop"  # {"until": time or null, "reason": ..., "told": bool}

# What the LinkedIn server says when LinkedIn pushes back, and whether waiting fixes it.
PUSHBACK: tuple[tuple[str, str, bool], ...] = (
    ("rate limit", "LinkedIn asked to slow down", True),
    (
        "restricted access",
        "LinkedIn restricted the account and asks for identity verification",
        False,
    ),
    ("checkpoint", "LinkedIn showed a security check", False),
    ("session expired", "the LinkedIn sign-in has expired", False),
    ("authentication", "LinkedIn needs you to sign in again", False),
    ("--login", "LinkedIn needs you to sign in again", False),
)


class Blocked(Exception):
    """LinkedIn wasn't called; the message says why, in a line for the owner."""


def clock(value: str) -> time:
    return time.fromisoformat(value)


def recent(now: datetime) -> list[datetime]:
    times = [datetime.fromisoformat(t) for t in json.loads(get_state(RECENT) or "[]")]
    return [t for t in times if t > now - timedelta(days=1)]


def stop_state() -> dict[str, Any]:
    raw = get_state(STOP)
    return json.loads(raw) if raw else {}


def stopped(now: datetime) -> str | None:
    """Why look-ups are stopped right now, or None."""
    stop = stop_state()
    if not stop:
        return None
    until = stop.get("until")
    if until is None:
        return f"{stop['reason']}. Send /linkedin on once that's sorted"
    if datetime.fromisoformat(until) > now:
        return f"{stop['reason']}, so look-ups wait until {datetime.fromisoformat(until):%a %H:%M}"
    return None


def check(tool: str, now: datetime, config: LinkedInConfig) -> float:
    """Seconds to wait before this look-up, or Blocked with the reason."""
    if tool not in READ_TOOLS:
        raise Blocked(f"{tool} isn't allowed: Mentor only reads LinkedIn.")
    if not config.enabled:
        raise Blocked("LinkedIn is switched off in the charter.")
    if reason := stopped(now):
        raise Blocked(f"LinkedIn is stopped: {reason}.")
    if not clock(config.start) <= now.time() < clock(config.end):
        raise Blocked(f"LinkedIn is used only between {config.start} and {config.end}.")
    calls = recent(now)
    today = [t for t in calls if t.astimezone(now.tzinfo).date() == now.date()]
    if len(today) >= config.daily_limit:
        raise Blocked(f"Today's {config.daily_limit} LinkedIn look-ups are used; more tomorrow.")
    last_hour = sorted(t for t in calls if t > now - timedelta(hours=1))
    if len(last_hour) >= config.hourly_limit:
        again = last_hour[0] + timedelta(hours=1)
        raise Blocked(
            f"{config.hourly_limit} LinkedIn look-ups in the last hour; more from "
            f"{again.astimezone(now.tzinfo):%H:%M}."
        )
    next_at = get_state(NEXT)
    return max(0.0, (datetime.fromisoformat(next_at) - now).total_seconds()) if next_at else 0.0


def record(
    now: datetime, config: LinkedInConfig, jitter: Callable[[], float] = random.random
) -> None:
    """Counts a look-up and sets the earliest time for the next one."""
    calls = [*recent(now), now]
    set_state(RECENT, json.dumps([t.isoformat() for t in calls]))
    gap = config.min_gap_seconds * (1 + jitter())
    set_state(NEXT, (now + timedelta(seconds=gap)).isoformat())


def pushback(text: str) -> tuple[str, bool] | None:
    """(reason, whether waiting fixes it) when an error says LinkedIn pushed back."""
    lowered = text.lower()
    for marker, reason, waiting_fixes in PUSHBACK:
        if marker in lowered:
            return reason, waiting_fixes
    return None


def pushback_in(result: Mapping[str, Any]) -> tuple[str, bool] | None:
    """A rate limit reported inside an answer (a section LinkedIn blocked)."""
    errors = result.get("section_errors") or {}
    items = errors.values() if isinstance(errors, Mapping) else errors
    for error in items:
        if isinstance(error, Mapping) and error.get("error_type") == "rate_limit":
            return "LinkedIn asked to slow down", True
    return None


def stop(now: datetime, reason: str, hours: float | None) -> None:
    """Stops look-ups for some hours, or (None) until the owner sends /linkedin on."""
    until = (now + timedelta(hours=hours)).isoformat() if hours else None
    set_state(STOP, json.dumps({"until": until, "reason": reason, "told": False}))


def switch_off(now: datetime) -> None:
    set_state(
        STOP, json.dumps({"until": None, "reason": "you switched LinkedIn off", "told": True})
    )


def switch_on() -> None:
    set_state(STOP, "")


def notice(now: datetime) -> str | None:
    """A message for the owner, once, when LinkedIn pushed back and look-ups stopped."""
    stop_now = stop_state()
    if not stop_now or stop_now.get("told") or not stopped(now):
        return None
    set_state(STOP, json.dumps({**stop_now, "told": True}))
    if stop_now.get("until"):
        return (
            f"⏸ {stop_now['reason']}, so I've paused LinkedIn look-ups until "
            f"{datetime.fromisoformat(stop_now['until']):%a %H:%M}. Nothing for you to do."
        )
    return (
        f"⏸ LinkedIn look-ups are stopped: {stop_now['reason']}. Open LinkedIn in your browser and "
        "do what it asks (sign in or verify). If it asks you to sign in again, also run "
        "`mentor linkedin --login` on the Mac. Then send /linkedin on."
    )


def status(now: datetime, config: LinkedInConfig) -> str:
    calls = recent(now)
    today = len([t for t in calls if t.astimezone(now.tzinfo).date() == now.date()])
    if not config.enabled:
        state = "switched off in the charter"
    elif reason := stopped(now):
        state = f"stopped: {reason}"
    else:
        state = "on"
    return (
        f"LinkedIn: {state}.\n"
        f"Look-ups today: {today} of {config.daily_limit} (at most {config.hourly_limit} an hour, "
        f"{config.start}–{config.end}).\n"
        "Read-only: you press Connect, Apply and Message yourself."
    )
