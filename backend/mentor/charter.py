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


class Charter(BaseModel):
    # Sections not used yet (workspaces, goals, budget, ...) are kept as plain data.
    model_config = ConfigDict(extra="allow")

    assistant: AssistantConfig
    owner: OwnerConfig
    job_search: JobSearchConfig = JobSearchConfig()
    practice: PracticeConfig = PracticeConfig()


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
