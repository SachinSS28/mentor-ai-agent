"""The 08:00 message: Dhamma for the day, the job shortlist, research & news, research
highlights (twice a week) and people to meet (weekly).

The Telegram bot runs `tick` every 15 minutes. The message goes out once a day at the charter's
digest_time, or at the first tick after it (e.g. when the Mac wakes up later).
"""

import logging
from datetime import datetime

from mentor.briefing import digest as briefing
from mentor.briefing import highlights
from mentor.charter import get_charter
from mentor.db.state import get_state, set_state
from mentor.jobs import desk
from mentor.linkedin import desk as linkedin
from mentor.network import desk as network
from mentor.practice import dhamma

log = logging.getLogger(__name__)


def tick(now: datetime) -> str | None:
    # Jobs first: a Google sign-in problem there is reported to the owner by the bot.
    desk.check_alerts()
    try:
        linkedin.morning_search(now)  # at 07:30: fresh LinkedIn jobs for the shortlist
    except Exception:
        log.exception("The morning LinkedIn search failed; the Job Desk carries on")
    desk.score_new()
    try:
        briefing.collect(now)
        briefing.score_new()
    except Exception:
        log.exception("Briefing collection failed; jobs carry on, briefing retries next tick")
    if not desk.digest_due(
        now, get_state(desk.DIGEST_SENT_ON), get_charter().job_search.digest_time
    ):
        return None
    parts: list[str | None] = []
    try:
        parts.append(dhamma.morning_section(now))  # first: an intention for the day
    except Exception:
        log.exception("Dhamma section failed; sending the rest")
    parts.append(desk.shortlist(now))
    try:
        parts.append(briefing.section(now))
    except Exception:
        log.exception("Briefing section failed; sending the job shortlist alone")
    try:
        parts.append(highlights.section(now))
    except Exception:
        log.exception("Research highlights failed; sending the rest")
    try:
        parts.append(network.section(now))
    except Exception:
        log.exception("People section failed; sending the rest")
    set_state(desk.DIGEST_SENT_ON, now.date().isoformat())
    return "\n\n".join(part for part in parts if part) or None
