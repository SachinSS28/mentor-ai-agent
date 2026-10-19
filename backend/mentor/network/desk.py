"""People to meet: weekly suggestions and connection follow-ups for the morning message.

Mentor never touches LinkedIn: it gives a LinkedIn search link and the owner connects.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

from mentor.charter import NetworkConfig, get_charter
from mentor.db.models import Person
from mentor.network import experts, store

HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-"})  # OpenAlex names use Unicode hyphens


def since(config: NetworkConfig, today: date) -> date:
    """Papers from 1 January, `years` years back, count."""
    return date(today.year - config.years, 1, 1)


def linkedin_search(person: Person) -> str:
    words = f"{person.name} {person.organization or ''}".strip().translate(HYPHENS)
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(words)}"


def format_person(person: Person) -> str:
    where = f" — {person.organization}" if person.organization else ""
    return f"**P{person.ref}** {person.name}{where}"


def person_block(person: Person) -> str:
    lines = [f"• {format_person(person)}"]
    if person.about:
        lines.append(f"  {person.about}")
    links = [f"[LinkedIn search]({linkedin_search(person)})"]
    if person.profile_url:
        links.append(f"[publications]({person.profile_url})")
    lines.append("  " + " · ".join(links))
    return "\n".join(lines)


def suggestions(
    now: datetime,
    topics: Mapping[str, Sequence[str]] | None = None,
    limit: int | None = None,
    only_new: bool = False,
    international_only: bool = False,
) -> list[Person]:
    """Searches OpenAlex and saves the best people as suggested contacts, with P-numbers."""
    config = get_charter().network
    start = since(config, now.date())
    exclude = store.known_ids() if only_new else frozenset()
    topics = topics or get_charter().research_topics
    found = experts.find(topics, start, config.prefer_international, exclude)
    if international_only:
        found = [c for c in found if c.abroad]
    return store.save_suggestions(found[: limit or config.suggest_size], start, now)


def section(now: datetime) -> str | None:
    """New people on the charter's suggest_day, and pending connection requests to check on."""
    config = get_charter().network
    parts = []
    if get_charter().research_topics and now.strftime("%A").lower() == config.suggest_day.lower():
        people = suggestions(now, only_new=True)
        if people:
            blocks = "\n".join(person_block(p) for p in people)
            parts.append(
                f"**People to meet this week**\n{blocks}\n"
                'Reply "note for P12" for a connection note, then "invited P12" once you send it.'
            )
    due = store.follow_ups_due(now.date())
    if due:
        lines = ["**Connection requests**"]
        lines += [
            f'• {format_person(p)}: accepted yet? Say "connected P{p.ref}" or "not now P{p.ref}".'
            for p in due
        ]
        parts.append("\n".join(lines))
        store.snooze([p.ref for p in due], now.date() + timedelta(days=config.follow_up_days))
    return "\n\n".join(parts) or None
