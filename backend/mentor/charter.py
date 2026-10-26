"""Loads config/charter.yaml: who Mentor works for and the rules it works by."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict

from mentor.settings import get_settings


class AssistantConfig(BaseModel):
    name: str = "Mentor"
    language: Literal["english", "hinglish", "hindi"] = "english"
    coaching_tone: Literal["gentle", "direct", "tough_love"] = "direct"


class OwnerConfig(BaseModel):
    name: str
    city: str
    timezone: str = "Asia/Hong_Kong"


class JobSearchConfig(BaseModel):
    """The Job Desk: what to look for, and when to send the shortlist."""

    focus: list[str] = []
    locations: list[str] = []
    digest_time: str = "08:00"  # owner's time zone
    min_score: int = 50  # jobs below this fit score stay out of the digest
    digest_size: int = 8
    follow_up_days: int = 7


class PracticeConfig(BaseModel):
    """Dhamma practice support: morning teaching, evening reflection, Uposatha reminders."""

    tradition: str = "Theravada"
    morning_dhamma: bool = True
    evening_reflection: bool = True
    evening_time: str = "21:30"  # owner's time zone
    uposatha: list[str] = ["new moon", "full moon"]


class NetworkConfig(BaseModel):
    """People to meet (on the charter's research_topics), and when to suggest them."""

    suggest_day: str = "Monday"  # weekly suggestions join that day's morning message
    suggest_size: int = 5
    years: int = 3  # papers from this many years back count
    prefer_international: bool = True  # careers that include institutions outside Hong Kong first
    follow_up_days: int = 7  # ask whether a connection request was accepted


class DocumentsConfig(BaseModel):
    """The one Google Drive folder Mentor may read (read-only)."""

    drive_folder: str = "Mentor"


class ReviewConfig(BaseModel):
    """The weekly review: progress, slips and next week's top three."""

    enabled: bool = True
    day: str = "Sunday"
    time: str = "19:00"  # owner's time zone


class CheckinConfig(BaseModel):
    """Short check-ins through the day: today's tasks, progress, and steps toward the goals."""

    enabled: bool = True
    every_hours: int = 2
    first: str = "10:00"  # owner's time zone
    last: str = "20:00"
    quiet_after_chat_minutes: int = 45  # skip a check-in while the owner is already talking


class ActivityConfig(BaseModel):
    """The activity journal: what's in front on the Mac once a minute, kept a few days."""

    enabled: bool = True
    every_seconds: int = 60
    idle_minutes: int = 5  # away from the keyboard this long: not recorded
    keep_days: int = 7
    # Recorded only as "(private)": no app name, title or site.
    private_apps: list[str] = []
    private_sites: list[str] = []  # parts of a web address, e.g. "hsbc" or "mail.google.com"
    # The app is recorded, its window titles (e.g. email subjects) aren't.
    hide_titles: list[str] = []


class Charter(BaseModel):
    # Sections not used yet (workspaces, goals, budget, ...) are kept as plain data.
    model_config = ConfigDict(extra="allow")

    assistant: AssistantConfig
    owner: OwnerConfig
    job_search: JobSearchConfig = JobSearchConfig()
    practice: PracticeConfig = PracticeConfig()
    network: NetworkConfig = NetworkConfig()
    documents: DocumentsConfig = DocumentsConfig()
    review: ReviewConfig = ReviewConfig()
    checkins: CheckinConfig = CheckinConfig()
    activity: ActivityConfig = ActivityConfig()
    # The owner's research topics as exact phrases ("a + b" needs both), for finding people and
    # the research highlights.
    research_topics: dict[str, list[str]] = {}


def load_charter(path: Path | None = None) -> Charter:
    path = path or get_settings().config_dir / "charter.yaml"
    return Charter.model_validate(yaml.safe_load(path.read_text()))


@lru_cache
def owner_profile_text() -> str:
    """config/profile.md: the owner's professional profile from their CV, or "" if not written."""
    path = get_settings().config_dir / "profile.md"
    return path.read_text().strip() if path.exists() else ""


@lru_cache
def get_charter() -> Charter:
    return load_charter()


def owner_timezone() -> ZoneInfo:
    return ZoneInfo(get_charter().owner.timezone)
