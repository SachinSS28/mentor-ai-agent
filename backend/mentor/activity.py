"""The activity journal: once a minute, the app and window (or browser tab) in front on the Mac,
so Mentor knows what the owner is working on. It runs on the Mac itself (`mentor watch`, a login
service), not in the container, and uses only built-in macOS tools: AppleScript and the idle timer.

Private apps and sites are stored as "(private)", minutes away from the keyboard aren't stored,
entries are deleted after `keep_days`, and "pause watching" stops it for a while. Safari doesn't
tell AppleScript which windows are private, so pause before private browsing in Safari.
"""

import logging
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import delete, select

from mentor.charter import ActivityConfig, get_charter, owner_timezone
from mentor.db.models import ActivityEntry
from mentor.db.session import get_sessionmaker
from mentor.db.state import get_state, set_state

log = logging.getLogger(__name__)

PRIVATE = "(private)"
PAUSED_UNTIL = "activity.paused_until"
GAP = timedelta(minutes=2)  # entries further apart start a new session
IDLE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')

FRONT_WINDOW = """
tell application "System Events"
    set frontProcess to first application process whose frontmost is true
    set appName to name of frontProcess
    set windowTitle to ""
    try
        set windowTitle to name of front window of frontProcess
    end try
end tell
return appName & tab & windowTitle
"""
# Browsers that tell AppleScript the current tab; Chromium ones also say whether it's incognito.
SAFARI_TAB = """
tell application "Safari" to tell front window
    return (name of current tab) & tab & (URL of current tab)
end tell
"""
CHROMIUM_TAB = """
tell application "{app}" to tell front window
    return (title of active tab) & tab & (URL of active tab) & tab & (mode as text)
end tell
"""
CHROMIUM = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Arc")


@dataclass(frozen=True)
class Seen:
    app: str
    title: str | None = None
    url: str | None = None
    incognito: bool = False


def osascript(script: str) -> str | None:
    try:
        done = subprocess.run(  # noqa: S603 (fixed scripts from this module)
            ["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=10
        )
    except subprocess.TimeoutExpired:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def idle_seconds() -> float:
    """Seconds since the last key press or mouse move."""
    out = subprocess.run(  # noqa: S603 (fixed command)
        ["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True
    )
    match = IDLE.search(out.stdout)
    return int(match.group(1)) / 1e9 if match else 0.0


def front() -> Seen | None:
    """What's in front now: the app, its window title, and for a browser the tab's address."""
    out = osascript(FRONT_WINDOW)
    if not out:
        return None
    app, _, title = out.partition("\t")
    if app == "Safari" and (tab := osascript(SAFARI_TAB)):
        name, _, url = tab.partition("\t")
        return Seen(app, name or title, url)
    if app in CHROMIUM and (tab := osascript(CHROMIUM_TAB.format(app=app))):
        name, url, mode = (tab.split("\t") + ["", ""])[:3]
        return Seen(app, name or title, url, mode == "incognito")
    return Seen(app, title or None)


def domain_of(url: str | None) -> str | None:
    host = urlparse(url or "").hostname
    return host.removeprefix("www.") if host else None


def entry(seen: Seen, config: ActivityConfig, at: datetime) -> ActivityEntry:
    """Applies the privacy rules to what was seen."""
    domain = domain_of(seen.url)
    private_app = seen.app.lower() in {a.lower() for a in config.private_apps}
    private_site = domain is not None and any(
        part.lower() in (seen.url or "").lower() for part in config.private_sites
    )
    if private_app or private_site or seen.incognito:
        return ActivityEntry(at=at, app=PRIVATE)
    hidden = seen.app.lower() in {a.lower() for a in config.hide_titles}
    title = None if hidden or not seen.title else seen.title[:200]
    return ActivityEntry(at=at, app=seen.app, title=title, domain=domain)


def paused(now: datetime) -> bool:
    until = get_state(PAUSED_UNTIL)
    return until is not None and datetime.fromisoformat(until) > now


def pause(now: datetime, hours: float) -> datetime | None:
    """Pauses the journal for `hours` (0 resumes). Returns when it resumes, or None."""
    until = now + timedelta(hours=hours) if hours > 0 else None
    set_state(PAUSED_UNTIL, (until or now).isoformat())
    return until


def record(item: ActivityEntry) -> None:
    with get_sessionmaker()() as session:
        session.add(item)
        session.commit()


def prune(now: datetime, keep_days: int) -> None:
    with get_sessionmaker()() as session:
        session.execute(
            delete(ActivityEntry).where(ActivityEntry.at < now - timedelta(days=keep_days))
        )
        session.commit()


def watch() -> None:
    """The login service: one entry a minute while the owner is at the Mac."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = get_charter().activity
    log.info(
        "Activity journal started: every %ds, private apps and sites excluded", config.every_seconds
    )
    last_prune = None
    while True:
        now = datetime.now(owner_timezone())
        try:
            present = idle_seconds() < config.idle_minutes * 60
            if config.enabled and not paused(now) and present and (seen := front()):
                record(entry(seen, config, now))
            if last_prune is None or now - last_prune > timedelta(hours=1):
                prune(now, config.keep_days)
                last_prune = now
        except Exception:
            log.exception("Couldn't record activity; trying again in a minute")
        time.sleep(config.every_seconds)


def label(item: ActivityEntry) -> str:
    if item.app == PRIVATE:
        return "Private"
    if item.title:
        title = item.title if len(item.title) <= 90 else item.title[:90] + "…"
        return f"{item.app} — {title}"
    return item.app


def entries(start: datetime, end: datetime) -> list[ActivityEntry]:
    query = select(ActivityEntry).where(ActivityEntry.at >= start, ActivityEntry.at < end)
    with get_sessionmaker()() as session:
        return list(session.scalars(query.order_by(ActivityEntry.at)))


def latest(now: datetime, within: timedelta = timedelta(minutes=3)) -> ActivityEntry | None:
    """What's in front on the Mac now: the newest entry, if it's recent."""
    query = select(ActivityEntry).where(ActivityEntry.at >= now - within, ActivityEntry.at <= now)
    with get_sessionmaker()() as session:
        return session.scalars(query.order_by(ActivityEntry.at.desc()).limit(1)).first()


def totals(items: list[ActivityEntry], every_seconds: int = 60) -> Counter[str]:
    """Minutes per activity (each entry stands for one sampling interval)."""
    minutes = every_seconds / 60
    counted: Counter[str] = Counter()
    for item in items:
        counted[label(item)] += minutes  # type: ignore[assignment]
    return counted


def duration(minutes: float) -> str:
    hours, mins = divmod(round(minutes), 60)
    return f"{hours} h {mins} min" if hours else f"{mins} min"


def summary(start: datetime, end: datetime, top: int = 8) -> str | None:
    items = entries(start, end)
    if not items:
        return None
    counted = totals(items, get_charter().activity.every_seconds)
    active = sum(counted.values())
    lines = [f"Active at the Mac: {duration(active)}"]
    lines += [f"- {name}: {duration(mins)}" for name, mins in counted.most_common(top)]
    return "\n".join(lines)


def period(name: str, now: datetime) -> tuple[datetime, datetime, str]:
    """(start, end, label) for "today", "yesterday", "last 2 hours" or "week"."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    key = name.strip().lower()
    if key == "yesterday":
        return midnight - timedelta(days=1), midnight, "yesterday"
    if key.startswith("week") or key in ("this week", "last 7 days"):
        return now - timedelta(days=7), now, "the last 7 days"
    if hours := re.match(r"last (\d+) hours?", key):
        return now - timedelta(hours=int(hours.group(1))), now, f"the last {hours.group(1)} hours"
    return midnight, now, "today"
