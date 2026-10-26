"""The morning's LinkedIn job searches (charter `linkedin.daily_searches`), saved to the Job Desk
so the 08:00 shortlist includes them. At most one run a day; it stops at the first look-up that
can't be made (Mac asleep, LinkedIn stopped, budget used)."""

import logging
from datetime import datetime

from mentor.charter import get_charter
from mentor.db.state import get_state, set_state
from mentor.jobs import store as jobs
from mentor.linkedin import results
from mentor.linkedin.client import LinkedInUnavailable, call
from mentor.linkedin.guard import clock

log = logging.getLogger(__name__)

SEARCHED_ON = "linkedin.searched_on"


def morning_due(now: datetime) -> bool:
    config = get_charter().linkedin
    return (
        config.enabled
        and bool(config.daily_searches)
        and clock(config.daily_search_time) <= now.time() < clock(config.end)
        and get_state(SEARCHED_ON) != now.date().isoformat()
    )


def morning_search(now: datetime) -> int:
    """Runs the morning's searches if they're due. Returns how many new jobs were found."""
    if not morning_due(now):
        return 0
    set_state(SEARCHED_ON, now.date().isoformat())  # once a day, even if it stops early
    charter = get_charter()
    locations: list[str | None] = [*charter.job_search.locations] or [None]
    new = 0
    for keywords in charter.linkedin.daily_searches:
        for location in locations:
            try:
                data = call(
                    "search_jobs", keywords=keywords, location=location, date_posted="past_24_hours"
                )
            except LinkedInUnavailable as error:
                log.warning("The morning LinkedIn search stopped: %s", error)
                return new
            _, added = jobs.save_searched(results.jobs(data, f"LinkedIn search: {keywords}"), now)
            new += added
    log.info("The morning LinkedIn search found %d new jobs", new)
    return new
