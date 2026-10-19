"""Parses LinkedIn job-alert emails into job postings.

Reads the plain-text part, where each job is a block of title / company / location, optional
lines such as "1 school alum", then "View job: <link>"; dashed lines separate the blocks.
"""

import re
from dataclasses import dataclass

LINKEDIN_ALERTS = "from:jobalerts-noreply@linkedin.com"

HEADER = re.compile(r"^Your job alert for (.+)$")
SEPARATOR = re.compile(r"^-{5,}$")
VIEW_JOB = re.compile(r"^View job:\s*https://www\.linkedin\.com/(?:\S*?/)?jobs/view/(\d+)")
TAG = re.compile(r"<[^>]+>")
# Lines around the job cards: links, leftover HTML, and LinkedIn's own headings.
NOISE = re.compile(
    r"https?://|<[^>]+>|^(?:new jobs?\b|a new job\b|your job alert|see all jobs|manage alerts|"
    r"edit alert|jobs? (?:you|from)\b|top job picks)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Posting:
    external_id: str
    title: str
    company: str
    location: str | None
    insights: tuple[str, ...] = ()
    alert: str | None = None

    @property
    def url(self) -> str:
        # The email's links carry tracking parameters; the job page works without them.
        return f"https://www.linkedin.com/jobs/view/{self.external_id}/"


def card(block: list[str], external_id: str, alert: str | None) -> Posting | None:
    """The first clean paragraph is the job (title, company, location); later lines are
    insights such as "3 connections"."""
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in [*block, ""]:
        if not line:
            if current:
                paragraphs.append(current)
            current = []
        elif not NOISE.search(line):
            current.append(line)
    if not paragraphs or len(paragraphs[0]) < 2:
        return None
    job, rest = paragraphs[0], [line for paragraph in paragraphs[1:] for line in paragraph]
    return Posting(
        external_id=external_id,
        title=job[0],
        company=job[1],
        location=job[2] if len(job) > 2 else None,
        insights=(*job[3:], *rest),
        alert=alert,
    )


def parse_linkedin_alert(text: str) -> list[Posting]:
    alert: str | None = None
    block: list[str] = []
    postings: list[Posting] = []
    for raw in text.splitlines():
        line = raw.strip()
        if alert is None and (header := HEADER.match(line)):
            alert = header.group(1)
        elif alert is None and TAG.search(line) and " jobs" in line:
            alert = TAG.sub("", line).strip()  # "<strong>water engineer</strong> jobs in Hong Kong"
        if SEPARATOR.match(line):
            block = []
        elif view := VIEW_JOB.match(line):
            if posting := card(block, view.group(1), alert):
                postings.append(posting)
            block = []
        else:
            block.append(line)
    return postings
