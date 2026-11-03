"""Word documents written with the standard library: valid parts, the text as written, and files
that stay in Documents/Mentor."""

import io
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest

from mentor import reading, word
from mentor.settings import get_settings

LETTER = """# Application

Sachin Shende
Hong Kong

Dear Hiring Manager,

I am applying for the **Senior Water Engineer** role at *Arup* & co <Ltd>.

- Network design and `EPANET` modelling
- Wastewater energy balance

1. Fifteen years in practice
2. Papers in Water Research

| Skill | Years |
|---|---|
| Hydraulics | 15 |

See [the posting](https://www.linkedin.com/jobs/view/1/)."""


@pytest.fixture
def documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("MENTOR_DOCUMENTS_DIR", str(tmp_path / "Mentor"))
    get_settings.cache_clear()
    yield tmp_path / "Mentor"
    get_settings.cache_clear()


def test_a_letter_becomes_a_valid_word_file() -> None:
    data = word.docx("Cover letter Arup", LETTER, "Sachin")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        for name in names:
            ElementTree.fromstring(archive.read(name))  # noqa: S314 (our own output)
        core = archive.read("docProps/core.xml").decode()
        numbering = archive.read("word/numbering.xml").decode()
    assert {"[Content_Types].xml", "word/document.xml", "word/styles.xml"} <= names
    assert "<dc:title>Cover letter Arup</dc:title>" in core
    assert '<w:num w:numId="2">' in numbering  # the numbered list has its own, restarting at 1
    text = reading.docx_text(data)
    assert text.strip().splitlines()[:3] == ["Application", "Sachin Shende", "Hong Kong"]
    assert "Senior Water Engineer role at Arup & co <Ltd>." in text
    assert "Fifteen years in practice" in text and "Hydraulics" in text
    assert "the posting (https://www.linkedin.com/jobs/view/1/)" in text


def test_documents_are_saved_beside_each_other_never_over(documents: Path) -> None:
    first = word.save('Cover letter: Arup/WSD "draft"', "Dear Sir,\n\nHello.")
    second = word.save('Cover letter: Arup/WSD "draft"', "Dear Madam,\n\nHello.")
    today = f"{datetime.now():%Y-%m-%d}"
    assert first.name == f"Cover letter Arup WSD draft ({today}).docx"
    assert second.name == f"Cover letter Arup WSD draft ({today}) 2.docx"
    assert word.find(first.name) == first.resolve()
    for outside in ("../secrets.docx", "Mentor/x.docx", "notes.txt", "", "missing.docx"):
        assert word.find(outside) is None, outside


def test_replies_link_saved_documents() -> None:
    path = Path("/x/Cover letter (2026-11-03).docx")
    link = word.link(path)
    assert link == "[Cover letter (2026-11-03).docx](/files/Cover%20letter%20%282026-11-03%29.docx)"
    assert word.linked_files(f"Saved it: {link}") == ["Cover letter (2026-11-03).docx"]


def test_mentor_saves_a_word_file_when_asked(documents: Path) -> None:
    from mentor.agent.tools import make_word_document

    reply = make_word_document.invoke(
        {"title": "CV points", "content": "- Led the WSD pilot\n" * 3}
    )
    assert reply.startswith("Saved the Word file in Documents/Mentor: [CV points (")
    assert len(list(documents.glob("*.docx"))) == 1
    short = make_word_document.invoke({"title": "x", "content": "hi"})
    assert short.startswith("Not saved")
