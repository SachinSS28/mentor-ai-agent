"""Turns the LinkedIn server's answers into Job Desk postings, contacts and short texts.

The server returns each page as raw text per section, plus the links it found on the page
("references": job, person, company and post links with their anchor text). Job titles and
people's names come from those links; the lines after them on the page give the company and place
of a job, or the headline and place of a person.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

from mentor.jobs.alerts import Posting

# LinkedIn's own links are relative ("/in/x/"); links the owner pastes are full addresses.
JOB_LINK = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})")
PROFILE_LINK = re.compile(r"(?:^|linkedin\.com)/in/([^/?#]+)")
COMPANY_LINK = re.compile(r"(?:^|linkedin\.com)/company/([^/?#]+)")
SITE = "https://www.linkedin.com"
VERIFIED = re.compile(r"\s+with verification$", re.IGNORECASE)
# "View Chan Tai Man's profile", "Chan Tai Man • 2nd" -> "Chan Tai Man"
PROFILE_LABEL = re.compile(r"^view\s+(.+?)[’']s\s+profile$", re.IGNORECASE)
DEGREE = re.compile(r"\s*[•·]?\s*(?:1st|2nd|3rd\+?)(?:\s+degree connection)?$", re.IGNORECASE)
# Lines on a results page that say nothing about the job or person themselves.
NOISE = re.compile(
    r"^(?:promoted|viewed|applied|saved|dismiss\b.*|new|verified|connect|message|follow|"
    r"following|pending|save|share|more|show all.*|[•·|]|"
    r"\d+\s*(?:second|minute|hour|day|week|month|year)s?\s+ago|"
    r"reposted\b.*|posted\b.*|status is \w+|view .*profile|(?:1st|2nd|3rd\+?)(?: degree.*)?|"
    r"view|feed post|… more|.* is a mutual connection|.*\bmutual connections?\b.*)$",
    re.IGNORECASE,
)
# Lines worth keeping with a job, as the alert emails' insights are.
INSIGHT = re.compile(
    r"connection|alum|applicant|easy apply|actively (?:reviewing|recruiting)|early applicant",
    re.IGNORECASE,
)
CARD_LINES = 8  # lines read after a title or name
# Free accounts get a monthly number of people searches; past it LinkedIn shows only a few.
SEARCH_LIMIT = re.compile(r"monthly limit for profile searches|commercial use limit", re.IGNORECASE)
CURRENT = re.compile(r"^current:\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class LinkedInPerson:
    username: str
    name: str
    headline: str | None = None
    location: str | None = None
    current: str | None = None  # "Senior Engineer at WSD", when LinkedIn shows it

    @property
    def url(self) -> str:
        return f"{SITE}/in/{self.username}/"

    @property
    def organization(self) -> str | None:
        """Where they work, when their current role or headline says ("... at WSD")."""
        for role in (self.current, self.headline):
            if role and " at " in role:
                return role.rsplit(" at ", 1)[1].strip(" .|") or None
        return None

    @property
    def about(self) -> str:
        now = f"now {self.current}" if self.current and self.current != self.headline else None
        return " · ".join(part for part in (self.headline, self.location, now) if part)


def references(data: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Every link the server found, from all sections."""
    found = data.get("references") or {}
    groups = found.values() if isinstance(found, Mapping) else [found]
    for group in groups:
        for item in group or []:
            if isinstance(item, Mapping) and item.get("url"):
                yield dict(item)


def page_lines(data: Mapping[str, Any]) -> list[str]:
    sections = data.get("sections") or {}
    text = "\n".join(str(t) for t in sections.values()) if isinstance(sections, Mapping) else ""
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def plain(line: str) -> str:
    """A line without LinkedIn's badges ("• 2nd", "with verification")."""
    return VERIFIED.sub("", DEGREE.sub("", line)).strip()


def absolute(url: str) -> str:
    return f"{SITE}{url}" if url.startswith("/") else url


def card(lines: list[str], anchor: str, others: set[str]) -> list[str] | None:
    """The useful lines after the line that is the anchor (a title or name), up to the next
    card's anchor. None when no line is the anchor (e.g. a name only mentioned in passing)."""
    key = anchor.casefold()
    for i, line in enumerate(lines):
        if plain(line).casefold() == key:
            following = []
            for later in lines[i + 1 : i + 1 + CARD_LINES]:
                text = plain(later)
                if text.casefold() in others:
                    break
                if text and text.casefold() != key and not NOISE.match(text):
                    following.append(text)
            return following
    return None


def job_titles(data: Mapping[str, Any]) -> dict[str, str]:
    """LinkedIn job ID -> title, from the job links on the page."""
    titles: dict[str, str] = {}
    for item in references(data):
        match = JOB_LINK.search(item["url"])
        text = VERIFIED.sub("", str(item.get("text") or "")).strip()
        if match and text and match.group(1) not in titles:
            titles[match.group(1)] = text
    return titles


def jobs(data: Mapping[str, Any], alert: str) -> list[Posting]:
    """The jobs in a search_jobs answer, in LinkedIn's order. Jobs whose title couldn't be read
    are left out rather than stored without one."""
    titles = job_titles(data)
    ids = [str(i) for i in data.get("job_ids") or []] or list(titles)
    lines = page_lines(data)
    others = {title.casefold() for title in titles.values()}
    postings = []
    for job_id in dict.fromkeys(ids):
        title = titles.get(job_id)
        if not title:
            continue
        rest = card(lines, title, others - {title.casefold()}) or []
        details = [line for line in rest if not INSIGHT.search(line)]
        postings.append(
            Posting(
                external_id=job_id,
                title=title,
                company=details[0] if details else "(company not shown)",
                location=details[1] if len(details) > 1 else None,
                insights=tuple(line for line in rest if INSIGHT.search(line))[:3],
                alert=alert,
            )
        )
    return postings


def person_name(text: str) -> str:
    text = text.strip()
    if label := PROFILE_LABEL.match(text):
        text = label.group(1)
    return DEGREE.sub("", text).strip()


def people(data: Mapping[str, Any]) -> list[LinkedInPerson]:
    """The people in a search_people answer, in LinkedIn's order."""
    names: dict[str, str] = {}
    for item in references(data):
        if item.get("kind") not in (None, "person"):
            continue
        match = PROFILE_LINK.search(item["url"])
        name = person_name(str(item.get("text") or ""))
        if match and name and not NOISE.match(name):
            names.setdefault(match.group(1), name)
    lines = page_lines(data)
    others = {name.casefold() for name in names.values()}
    found = []
    for username, name in names.items():
        rest = card(lines, name, others - {name.casefold()})
        if rest is None:  # e.g. "Yusuf Fidan is a mutual connection"
            continue
        current = next((m.group(1) for line in rest if (m := CURRENT.match(line))), None)
        details = [line for line in rest if not CURRENT.match(line)]
        found.append(
            LinkedInPerson(
                username=username,
                name=name,
                headline=details[0] if details else None,
                location=details[1] if len(details) > 1 else None,
                current=current,
            )
        )
    return found


def search_limited(data: Mapping[str, Any]) -> bool:
    """Whether LinkedIn cut a people search short (the account's monthly limit)."""
    return any(SEARCH_LIMIT.search(line) for line in page_lines(data))


def post_links(data: Mapping[str, Any], limit: int = 10) -> list[str]:
    links = [absolute(i["url"]) for i in references(data) if i.get("kind") == "feed_post"]
    return list(dict.fromkeys(links))[:limit]


def page_text(data: Mapping[str, Any], limit: int) -> str:
    """The page's sections as plain text, cut to about `limit` characters."""
    sections = data.get("sections") or {}
    parts = [f"[{name}]\n{str(text).strip()}" for name, text in dict(sections).items() if text]
    text = "\n\n".join(parts)
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n…"


def date_posted(days: int) -> str | None:
    """search_jobs' recency filter for "posted in the last N days"."""
    if days <= 1:
        return "past_24_hours"
    if days <= 7:
        return "past_week"
    return "past_month" if days <= 31 else None


def job_id(text: str) -> str | None:
    """A LinkedIn job ID from a job link or a bare ID."""
    if match := JOB_LINK.search(text):
        return match.group(1)
    digits = text.strip()
    return digits if digits.isdigit() and len(digits) >= 6 else None


def profile_username(text: str) -> str | None:
    match = PROFILE_LINK.search(text)
    return match.group(1) if match else None


def company_slug(text: str) -> str | None:
    match = COMPANY_LINK.search(text)
    return match.group(1) if match else None


def search_link(kind: str, words: str, location: str = "") -> str:
    """LinkedIn's own search page for jobs, people or posts, for the owner to open."""
    query = quote_plus(words.strip())
    if kind == "jobs":
        place = f"&location={quote_plus(location.strip())}" if location.strip() else ""
        return f"{SITE}/jobs/search/?keywords={query}{place}"
    if kind == "people":
        return f"{SITE}/search/results/people/?keywords={query}"
    return f"{SITE}/search/results/content/?keywords={query}"
