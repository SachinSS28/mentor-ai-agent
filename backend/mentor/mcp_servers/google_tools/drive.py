"""Google Drive, read-only and limited to the one folder the owner chose for Mentor (the charter's
documents.drive_folder): CVs, publication lists, statements, cover letters, scans and images."""

from dataclasses import dataclass
from typing import Any

from googleapiclient.discovery import build

from mentor.mcp_servers.google_tools.auth import (
    DRIVE_READONLY,
    GoogleNotConnectedError,
    get_credentials,
    has_scope,
)
from mentor.reading import DOCX, IMAGE_TYPES, docx_text, read_image, read_pdf, tidy

FOLDER = "application/vnd.google-apps.folder"
PDF = "application/pdf"
EXPORTS = {  # Google's own formats, exported as text
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
DOC_CHARS = 12000  # about 3,000 tokens: a long CV fits, a thesis doesn't
MAX_BYTES = 15_000_000
OTHER_IMAGES = ("image/gif", "image/bmp", "image/tiff")  # converted to PNG first


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


def text_of(service: Any, f: DriveFile) -> str:
    if f.mime_type in EXPORTS:
        data = service.files().export(fileId=f.id, mimeType=EXPORTS[f.mime_type]).execute()
        return bytes(data).decode("utf-8", errors="replace")
    if f.size is not None and f.size > MAX_BYTES:
        raise ValueError(f"{f.name} is too large to read ({f.size // 1_000_000} MB)")
    readable = (PDF, DOCX, *IMAGE_TYPES) + OTHER_IMAGES
    if f.mime_type not in readable and not f.mime_type.startswith("text/"):
        raise ValueError(f"{f.name} is a {f.mime_type} file, which Mentor can't read")
    data = bytes(service.files().get_media(fileId=f.id).execute())
    if f.mime_type == PDF:
        return read_pdf(data)
    if f.mime_type == DOCX:
        return docx_text(data)
    if f.mime_type.startswith("image/"):
        return read_image(data, f.mime_type, "image")
    return data.decode("utf-8", errors="replace")


def read(folder: str, name: str) -> tuple[DriveFile, str]:
    service = drive_service()
    f = pick(list_files(service, folder), name)
    return f, tidy(text_of(service, f), DOC_CHARS)
