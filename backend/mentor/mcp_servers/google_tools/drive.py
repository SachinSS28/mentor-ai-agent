"""Google Drive, read-only and limited to the one folder the owner chose for Mentor (the charter's
documents.drive_folder): CVs, publication lists, statements, past cover letters."""

import html
import io
import re
import zipfile
from dataclasses import dataclass
from typing import Any

from googleapiclient.discovery import build
from pypdf import PdfReader

from mentor.mcp_servers.google_tools.auth import (
    DRIVE_READONLY,
    GoogleNotConnectedError,
    get_credentials,
    has_scope,
)

FOLDER = "application/vnd.google-apps.folder"
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EXPORTS = {  # Google's own formats, exported as text
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
DOC_CHARS = 12000  # about 3,000 tokens: a long CV fits, a thesis doesn't
MAX_BYTES = 15_000_000
WORD_PARAGRAPH = re.compile(r"</w:p>")
XML_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified: str  # e.g. "2026-09-30T08:15:00.000Z"
    size: int | None = None


def drive_service() -> Any:
    creds = get_credentials()
    if not has_scope(creds, DRIVE_READONLY):
        raise GoogleNotConnectedError(
            "Mentor needs permission to read your Drive folder. Reconnect Google on the Mac with: "
            "uv run mentor google-auth"
        )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def quoted(name: str) -> str:
    return "'" + name.replace("\\", "\\\\").replace("'", "\\'") + "'"


def folder_id(service: Any, name: str) -> str:
    query = f"mimeType = '{FOLDER}' and name = {quoted(name)} and trashed = false"
    found = service.files().list(q=query, fields="files(id)", pageSize=1).execute()
    if not found.get("files"):
        raise LookupError(
            f"There's no Google Drive folder named {name!r} yet. Create it and put the documents "
            "Mentor may read in it."
        )
    return str(found["files"][0]["id"])


def list_files(service: Any, folder: str) -> list[DriveFile]:
    query = f"{quoted(folder_id(service, folder))} in parents and trashed = false"
    response = (
        service.files()
        .list(
            q=f"{query} and mimeType != '{FOLDER}'",
            fields="files(id,name,mimeType,modifiedTime,size)",
            orderBy="modifiedTime desc",
            pageSize=100,
        )
        .execute()
    )
    return [
        DriveFile(f["id"], f["name"], f["mimeType"], f.get("modifiedTime", ""), int(f["size"]))
        if f.get("size")
        else DriveFile(f["id"], f["name"], f["mimeType"], f.get("modifiedTime", ""))
        for f in response.get("files", [])
    ]


def pick(files: list[DriveFile], name: str) -> DriveFile:
    """The file with this exact name, else the newest whose name contains every word."""
    wanted = name.strip().lower()
    for f in files:
        if f.name.lower() == wanted:
            return f
    words = wanted.split()
    matches = [f for f in files if all(w in f.name.lower() for w in words)]
    if not matches:
        names = ", ".join(f.name for f in files) or "nothing yet"
        raise LookupError(f"No document matching {name!r}. In the folder: {names}")
    return matches[0]


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    paragraphs = XML_TAG.sub("", WORD_PARAGRAPH.sub("\n", xml))
    return html.unescape(paragraphs)


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def text_of(service: Any, f: DriveFile) -> str:
    if f.mime_type in EXPORTS:
        data = service.files().export(fileId=f.id, mimeType=EXPORTS[f.mime_type]).execute()
        return bytes(data).decode("utf-8", errors="replace")
    if f.size is not None and f.size > MAX_BYTES:
        raise ValueError(f"{f.name} is too large to read ({f.size // 1_000_000} MB)")
    if f.mime_type not in (PDF, DOCX) and not f.mime_type.startswith("text/"):
        raise ValueError(f"{f.name} is a {f.mime_type} file, which Mentor can't read")
    data = bytes(service.files().get_media(fileId=f.id).execute())
    if f.mime_type == PDF:
        return pdf_text(data)
    if f.mime_type == DOCX:
        return docx_text(data)
    return data.decode("utf-8", errors="replace")


def tidy(text: str, limit: int = DOC_CHARS) -> str:
    """Blank runs collapsed, cut at a word boundary."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    joined = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return joined if len(joined) <= limit else joined[:limit].rsplit(" ", 1)[0] + " …"


def read(folder: str, name: str) -> tuple[DriveFile, str]:
    service = drive_service()
    f = pick(list_files(service, folder), name)
    return f, tidy(text_of(service, f))
