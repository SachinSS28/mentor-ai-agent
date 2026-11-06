"""The CV maker: the master CV's facts in the owner's layout, and only checked choices from the
model. Rendered from the real config/cv.yaml; the contact block is made up."""

import html
import io
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from mentor import cv, reading
from mentor.db.models import Job
from mentor.settings import get_settings

CONTACT = """lines:
  - 1 Example Road, Kowloon, Hong Kong SAR
  - "Mobile: (+852) 0000 0000 | Email: someone@example.org"
  - "LinkedIn: linkedin.com/in/example-person | ORCID: 0000-0000-0000-0000"
"""
POSTING = (
    "Novva Group is hiring a Technical Due Diligence Manager in Hong Kong to underwrite solar "
    "PV, wind and hydro assets powering data centers. You will review EPC and O&M contracts, "
    "energy yield studies and red-flag reports for investment committees. "
) * 2
GOOD_REPLY = """**VERSION:** energy
COMPANY: Novva Group
ROLE: Chief Wizard
SKILLS: power, made_up, Due Diligence, power
FIT: Fully aligned with Novva Group's mission to underwrite bankable clean energy assets."""


def fake_model(reply: str = GOOD_REPLY, fails: bool = False, *more: str) -> Any:
    replies = iter([reply, *more])

    def invoke(messages: Any) -> AIMessage:
        if fails:
            raise ConnectionError("Ollama is down")
        return AIMessage(next(replies, reply))

    return SimpleNamespace(invoke=invoke)


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "cv_contact.yaml").write_text(CONTACT)
    monkeypatch.setenv("MENTOR_DOCUMENTS_DIR", str(tmp_path / "Mentor"))
    monkeypatch.setenv("MENTOR_CV_CONTACT_PATH", str(tmp_path / "cv_contact.yaml"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def parts(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        found = {name: archive.read(name).decode() for name in archive.namelist()}
    for xml in found.values():
        ElementTree.fromstring(xml)  # noqa: S314 (our own output)
    return found


def test_the_master_cv_is_complete_and_keeps_contact_details_out() -> None:
    master = cv.load()
    assert set(master.versions) == {"energy", "water", "academic"}
    assert all(len(v.skills) == 6 for v in master.versions.values())
    text = (get_settings().config_dir / "cv.yaml").read_text()
    assert not re.search(r"[\w.]+@[\w.]+|\+852|Mobile:", text)  # those live on the Mac only


@pytest.mark.parametrize("version", ["energy", "water", "academic"])
def test_each_version_is_a_valid_cv_in_the_owners_layout(version: str, files: Path) -> None:
    master = cv.load()
    data = cv.render(master, cv.plain(master, version), cv.contact())
    found = parts(data)
    document = found["word/document.xml"]
    text = reading.docx_text(data)
    lines = text.strip().splitlines()
    assert lines[0] == "SACHIN SHENDE, PhD"
    assert lines[1] == master.versions[version].headline
    assert "Mobile: (+852) 0000 0000 | Email: someone@example.org" in text
    section = r'<w:pStyle w:val="CVSection"/></w:pPr><w:r><w:t[^>]*>([^<]+)'
    headings = [html.unescape(h) for h in re.findall(section, document)]
    assert headings[:2] == [
        master.versions[version].profile_heading.upper(),
        master.versions[version].skills_heading.upper(),
    ]
    assert "PROFESSIONAL EXPERIENCE" in headings and "EDUCATION & ACADEMIC CREDENTIALS" in headings
    skills = [master.skills[key].title for key in master.versions[version].skills]
    assert all(title in text for title in skills)
    assert document.count(f'w:fill="{cv.SHADE}"') >= 6  # the shaded skills panel
    assert ("Capacity / Valuation" in text) == (version == "energy")
    assert "<w:titlePg/>" in document  # no running header on the first page
    assert "Sachin Shende, PhD" in found["word/header1.xml"]  # from page 2
    assert 'w:instr=" PAGE "' in found["word/footer1.xml"]
    relations = found["word/_rels/document.xml.rels"]
    assert 'Target="https://www.linkedin.com/in/example-person" TargetMode="External"' in relations
    assert 'Target="mailto:someone@example.org" TargetMode="External"' in relations
    assert "wordprocessingml.header+xml" in found["[Content_Types].xml"]


def test_tailoring_keeps_only_what_checks_out(files: Path) -> None:
    master = cv.load()
    choice = cv.tailor(master, POSTING, fake_model())
    assert choice.version == "energy" and choice.company == "Novva Group"
    assert choice.role == ""  # not in the posting: never put on the CV
    assert choice.skills[:2] == ("power", "due_diligence")  # unknown and repeated keys dropped
    assert len(choice.skills) == 6
    text = reading.docx_text(cv.render(master, choice, []))
    assert "EXECUTIVE PROFILE & VALUE PROPOSITION FOR NOVVA GROUP" in text
    assert "(ADB, JBIC, EXIM Bank). Fully aligned with Novva Group's mission" in text


@pytest.mark.parametrize(
    "fit",
    [
        "Brings proven expertise to [Company Name]'s energy portfolio and investment goals.",
        "I am confident my hydropower background suits your underwriting team well.",
        "Great fit.",
        "Sachin Shende has 12+ years of experience in TDD for energy and infrastructure assets.",
        "The candidate possesses 12+ years of international experience in water utilities.",
        "Brings 12+ years of international experience in water utilities and hydraulic modeling.",
    ],
)
def test_a_fit_sentence_that_isnt_cv_text_is_left_out(fit: str) -> None:
    choice = cv.tailor(cv.load(), POSTING, fake_model(f"VERSION: energy\nFIT: {fit}"))
    assert choice.fit == ""


def test_a_fit_sentence_gets_one_rewrite() -> None:
    first = "VERSION: water\nFIT: Fully aligned with Black & Veatch's needs, after 12+ years."
    rewrite = "Fully aligned with Black & Veatch's needs in water supply and hydraulic modeling."
    choice = cv.tailor(cv.load(), "Black & Veatch water job", fake_model(first, False, rewrite))
    assert choice.fit == rewrite
    stubborn = fake_model(first, False, "Offering 12+ years in water utilities to Black & Veatch.")
    assert cv.tailor(cv.load(), "Black & Veatch water job", stubborn).fit == ""


def test_without_the_model_the_keywords_pick_the_version() -> None:
    master = cv.load()
    water = "Senior Engineer, wastewater treatment plant upgrade and hydraulic modelling for DSD."
    assert cv.tailor(master, water, fake_model(fails=True)) == cv.plain(master, "water")
    assert cv.by_keywords(master, POSTING) == "energy"
    assert cv.by_keywords(master, "Assistant Professor, teaching and research") == "academic"
    assert cv.by_keywords(master, "") == master.default_version


def test_a_cv_without_contact_details_says_so(files: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mentor.agent import tools

    (files / "cv_contact.yaml").unlink()
    monkeypatch.setattr(tools, "get_chat_model", lambda role: fake_model(fails=True))
    state = {"messages": [HumanMessage("Make my CV for academic jobs")]}
    reply = tools.make_cv.invoke({"state": state, "version": "academic"})
    assert reply.startswith("Saved Sachin's CV (Academic & research version) in Documents/Mentor:")
    assert "contact details are missing" in reply
    saved = list((files / "Mentor").glob("*.docx"))
    assert [p.name.split(" (")[0] for p in saved] == ["Sachin Shende CV - Academic & research"]


def test_mentor_tailors_a_cv_to_the_posting_in_the_message(
    files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mentor.agent import tools

    monkeypatch.setattr(tools, "get_chat_model", lambda role: fake_model())
    state = {"messages": [HumanMessage(f"Prepare my CV for this job:\n\n{POSTING}")]}
    reply = tools.make_cv.invoke({"state": state})
    assert "(Energy & power infrastructure version, for Novva Group)" in reply
    assert "[Sachin Shende CV - Novva Group (" in reply
    assert 'the profile ends: "Fully aligned with Novva Group' in reply
    saved = next((files / "Mentor").glob("*.docx"))
    assert "VALUE PROPOSITION FOR NOVVA GROUP" in reading.docx_text(saved.read_bytes())


def test_a_job_number_brings_the_jobs_details(monkeypatch: pytest.MonkeyPatch) -> None:
    from mentor.agent import tools

    job = Job(ref=12, title="Senior Hydraulic Engineer", company="AECOM", location="Hong Kong")
    job.fit_reason = "Water network modelling with EPANET."
    monkeypatch.setattr(tools.store, "by_ref", lambda ref: job if ref == 12 else None)
    assert tools.job_details("J12") == (
        "Senior Hydraulic Engineer at AECOM, Hong Kong. Water network modelling with EPANET."
    )
    assert tools.job_details("J99") == "J99"  # unknown: as written
    assert tools.job_details("Arup water engineer") == "Arup water engineer"


def test_a_cv_never_goes_through_the_plain_word_tool(files: Path) -> None:
    from mentor.agent.tools import make_word_document

    reply = make_word_document.invoke({"title": "Sachin CV", "content": "- Led the pilot\n" * 3})
    assert reply.startswith("Not saved: a CV is made with make_cv")
    letter = make_word_document.invoke(
        {"title": "Cover letter for the CV", "content": "Dear Sir," * 5}
    )
    assert letter.startswith("Saved the Word file")
