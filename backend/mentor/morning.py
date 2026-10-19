"""The 08:00 message: the job shortlist plus research & news, and the routines that feed them.

The Telegram bot runs `tick` every 15 minutes. The message goes out once a day at the charter's
digest_time, or at the first tick after it (e.g. when the Mac wakes up later).
"""

import logging
from datetime import datetime

from mentor.briefing import digest as briefing
from mentor.charter import get_charter
from mentor.db.state import get_state, set_state
from mentor.jobs import desk

log = logging.getLogger(__name__)


def tick(now: datetime) -> str | None:
    # Jobs first: a Google sign-in problem there is reported to the owner by the bot.
    desk.check_alerts()
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
    parts = [desk.shortlist(now)]
    try:
        parts.append(briefing.section(now))
    except Exception:
        log.exception("Briefing section failed; sending the job shortlist alone")
    set_state(desk.DIGEST_SENT_ON, now.date().isoformat())
    return "\n\n".join(part for part in parts if part) or None
