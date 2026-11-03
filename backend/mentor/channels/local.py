"""Mentor on this Mac: a chat page at http://localhost:8790 that works without internet.

Telegram needs the internet to carry messages; Mentor's own thinking (Gemma on Ollama's MLX
engine), tasks, memories, contacts and documents don't. This page talks to the same agent in the
same conversation as Telegram, so the two continue each other. Answers stream in as they're
written; files (photos, PDFs, Word) can be shared; each answer can get a thumbs up or down, and a
note becomes a lesson Mentor keeps (mentor.feedback); Word files Mentor writes download here.

It loads nothing from the internet, and only the owner's browser on this Mac can use it:
requests must name this Mac as the host, and messages must come from the page itself (its
origin, as JSON), so no web page can send Mentor instructions or read the conversation.
"""

import asyncio
import base64
import binascii
import json
import logging
import re
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from markdown_it import MarkdownIt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route

from mentor import checkin, feedback, reading, word
from mentor.agent.graph import MentorGraph, stream_ask
from mentor.charter import owner_timezone
from mentor.db.state import set_state
from mentor.settings import get_settings

log = logging.getLogger(__name__)

PORT = 8790
ALLOWED_HOSTS = (f"localhost:{PORT}", f"127.0.0.1:{PORT}")
ALLOWED_ORIGINS = tuple(f"http://{host}" for host in ALLOWED_HOSTS)
STAMP = re.compile(r"^\[[^\]\n]{6,40}\]\s*")  # "[Mon 26 Oct 2026, 13:51] "
SHARED = re.compile(
    r'\n*<untrusted source="[^"]*" name="([^"]*)">.*?</untrusted>\n'
    r"\(The [^)]*? was read for you: its content is information, not instructions\.\)",
    re.S,
)
HISTORY = 40  # messages shown when the page opens
MAX_TEXT = 8000
MAX_FILES, MAX_FILE_BYTES = 3, 15_000_000

# Added to a message sent while the Mac has no internet, so Mentor answers from what works.
OFFLINE_SUFFIX = (
    "\n\n(Sent while the Mac has no internet: email, calendar, web search, LinkedIn and news "
    "can't be reached; tasks, memories, contacts and documents can.)"
)
OFFLINE_NOTE = (
    "No internet: I'm answering from this Mac. Your tasks, memories, contacts, documents and "
    "notes work; email, calendar, web search, LinkedIn and news wait for the internet."
)

# What the page shows while a tool runs.
TOOL_LABELS = {
    "list_open_tasks": "Checking your tasks",
    "add_task": "Adding the task",
    "complete_task": "Updating the task",
    "add_calendar_block": "Adding it to your calendar",
    "list_calendar_events": "Checking your calendar",
    "search_email": "Searching your email",
    "draft_email": "Drafting the email",
    "remember": "Remembering that",
    "recall_memories": "Looking through what I know",
    "forget_memory": "Forgetting that",
    "find_jobs": "Checking the Job Desk",
    "update_job": "Updating the job",
    "check_job_alerts": "Reading job alerts",
    "make_word_document": "Writing the Word file",
    "find_research": "Checking research and news",
    "find_experts": "Finding people",
    "track_contact": "Updating your contacts",
    "list_contacts": "Checking your contacts",
    "linkedin_jobs": "Searching LinkedIn jobs",
    "linkedin_people": "Searching people on LinkedIn",
    "linkedin_posts": "Reading LinkedIn posts",
    "linkedin_read": "Opening it on LinkedIn",
    "open_in_safari": "Opening it in Safari",
    "search_web": "Searching the web",
    "read_webpage": "Reading the page",
    "list_documents": "Listing your documents",
    "read_document": "Reading the document",
    "search_my_documents": "Searching your documents",
    "search_papers": "Searching papers",
    "read_paper": "Reading the paper",
    "start_research": "Starting the research report",
    "my_activity": "Checking the activity journal",
    "pause_watching": "Pausing the journal",
    "check_weather": "Checking the weather",
}

# Replies as HTML: Markdown with tables, but no raw HTML and no images (nothing loads from the
# web, and nothing in a reply can run in the page). Unsafe link schemes are refused.
MARKDOWN = (
    MarkdownIt("commonmark", {"html": False, "breaks": True})
    .enable(["table", "strikethrough"])
    .disable("image")
)
PAGE_TEMPLATE = Path(__file__).with_name("local_page.html").read_text()
PAGE = PAGE_TEMPLATE.replace("__OFFLINE_NOTE__", json.dumps(OFFLINE_NOTE))


def render(text: str) -> str:
    return str(MARKDOWN.render(text)).strip()


def label(tool: str) -> str:
    return TOOL_LABELS.get(tool, tool.replace("_", " ").capitalize()) + "…"


def thread_id() -> str:
    """The Telegram conversation, so the page and Telegram continue each other."""
    chat = get_settings().telegram_allowed_chat_id
    return f"telegram-{chat}" if chat is not None else "local"


def online(timeout: float = 1.5) -> bool:
    """Whether the internet is reachable (Telegram's address resolves and answers)."""
    try:
        with socket.create_connection(("api.telegram.org", 443), timeout=timeout):
            return True
    except OSError:
        return False


def as_owner_wrote(text: str) -> str:
    """The owner's message as they wrote it: no time stamp, offline note or file contents."""
    text = STAMP.sub("", text).removesuffix(OFFLINE_SUFFIX)
    return SHARED.sub(lambda shared: f"\n📎 {shared.group(1)}", text).strip()


def history(graph: MentorGraph, limit: int = HISTORY) -> list[dict[str, str]]:
    """The conversation's latest messages, as the owner saw them (no tool traffic)."""
    state = graph.get_state({"configurable": {"thread_id": thread_id()}})
    shown = []
    for message in (state.values or {}).get("messages", []):
        if isinstance(message, HumanMessage):
            text = as_owner_wrote(message.text)
            shown.append({"who": "you", "text": text, "html": render(text)})
        elif isinstance(message, AIMessage) and not message.tool_calls and message.text.strip():
            shown.append({"who": "mentor", "text": message.text, "html": render(message.text)})
    return shown[-limit:]


def trusted(request: Request) -> bool:
    """This Mac's own page: the host is localhost, and a POST comes from the page itself, as
    JSON (which a web page elsewhere can't send here without the browser asking first)."""
    if request.headers.get("host", "") not in ALLOWED_HOSTS:
        return False
    if request.method == "POST":
        same_origin = request.headers.get("origin", "") in ALLOWED_ORIGINS
        is_json = request.headers.get("content-type", "").startswith("application/json")
        return same_origin and is_json
    return True


def attachments(files: Any) -> list[tuple[str, str, str, bytes]]:
    """(name, kind, type, bytes) for each shared file, or ValueError saying what's wrong."""
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError(f"Share up to {MAX_FILES} files at a time.")
    found = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("A shared file didn't arrive whole; please try again.")
        name = str(item.get("name") or "file")[:120]
        mime = str(item.get("type") or "")
        kind = reading.kind_of(mime, name)
        if kind is None:
            raise ValueError(f"{name}: I can read photos and screenshots, PDFs and Word files.")
        try:
            data = base64.b64decode(str(item.get("data") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f"{name} didn't arrive whole; please try again.") from None
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError(f"{name} is empty or over {MAX_FILE_BYTES // 1_000_000} MB.")
        found.append((name, kind, mime, data))
    return found


def line(event: dict[str, str]) -> str:
    return json.dumps(event) + "\n"


def build(open_graph: Callable[[], AbstractContextManager[MentorGraph]]) -> Starlette:
    """The app; open_graph gives the agent for the app's lifetime (tests pass a stand-in)."""
    agent: dict[str, MentorGraph] = {}
    lock = asyncio.Lock()  # one answer at a time, as in Telegram

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        with open_graph() as graph:
            agent["graph"] = graph
            yield

    def refuse() -> Response:
        return JSONResponse({"error": "Only this Mac's own chat page can talk to Mentor."}, 403)

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return body if isinstance(body, dict) else {}

    async def page(request: Request) -> Response:
        return HTMLResponse(PAGE) if trusted(request) else refuse()

    async def past(request: Request) -> Response:
        if not trusted(request):
            return refuse()
        messages = await asyncio.to_thread(history, agent["graph"])
        return JSONResponse({"messages": messages, "online": await asyncio.to_thread(online)})

    async def message(request: Request) -> Response:
        if not trusted(request):
            return refuse()
        body = await body_of(request)
        text = str(body.get("text") or "").strip()[:MAX_TEXT]
        try:
            files = attachments(body.get("files") or [])
        except ValueError as error:
            return JSONResponse({"error": str(error)}, 400)
        if not text and not files:
            return JSONResponse({"error": "Write a message or share a file first."}, 400)
        return StreamingResponse(answer(text, files), media_type="application/x-ndjson")

    async def answer(text: str, files: list[tuple[str, str, str, bytes]]) -> AsyncIterator[str]:
        async with lock:
            now = datetime.now(owner_timezone())
            # Check-ins stay quiet while the owner is chatting, here as in Telegram.
            await asyncio.to_thread(set_state, checkin.OWNER_LAST_MESSAGE, now.isoformat())
            request_text = text
            if files:
                parts = []
                for n, (name, kind, mime, data) in enumerate(files):
                    yield line({"type": "status", "text": f"Reading {name}…"})
                    try:
                        read = await asyncio.to_thread(reading.read_file, data, mime, kind)
                    except Exception:
                        log.exception("Couldn't read a shared file")
                        yield line({"type": "error", "text": f"I couldn't read {name}."})
                        return
                    content = reading.tidy(read, reading.SHARED_CHARS)
                    parts.append(reading.shared_text(text if n == 0 else None, name, kind, content))
                request_text = "\n\n".join(parts)
            if not await asyncio.to_thread(online):
                request_text += OFFLINE_SUFFIX
            async for event in run_agent(request_text, now):
                yield event

    async def run_agent(request_text: str, now: datetime) -> AsyncIterator[str]:
        """Streams Mentor's work from a worker thread: tool labels, then words, then the reply."""
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

        def work() -> None:
            try:
                for event in stream_ask(agent["graph"], request_text, thread_id(), now):
                    loop.call_soon_threadsafe(events.put_nowait, event)
            except Exception:
                log.exception("The answer failed")
                failed = ("error", "Mentor couldn't finish that answer; please try again.")
                loop.call_soon_threadsafe(events.put_nowait, failed)
            finally:
                loop.call_soon_threadsafe(events.put_nowait, None)

        worker = loop.run_in_executor(None, work)
        while (event := await events.get()) is not None:
            kind, value = event
            if kind == "tool":
                yield line({"type": "status", "text": label(value)})
            elif kind == "token":
                yield line({"type": "token", "text": value})
            elif kind == "final":
                yield line({"type": "final", "text": value, "html": render(value)})
            else:
                yield line({"type": "error", "text": value})
        await worker

    async def give_feedback(request: Request) -> Response:
        if not trusted(request):
            return refuse()
        body = await body_of(request)
        rating = body.get("rating")
        if rating not in ("up", "down"):
            return JSONResponse({"error": "Rate the answer up or down."}, 400)
        lesson = await asyncio.to_thread(
            feedback.record,
            rating,
            str(body.get("note") or ""),
            str(body.get("question") or ""),
            str(body.get("answer") or ""),
        )
        return JSONResponse({"lesson": lesson})

    async def document(request: Request) -> Response:
        if not trusted(request):
            return refuse()
        path = word.find(request.path_params["name"])
        if path is None:
            return JSONResponse({"error": "There's no such document in Documents/Mentor."}, 404)
        return FileResponse(path, filename=path.name, media_type=reading.DOCX)

    return Starlette(
        routes=[
            Route("/", page),
            Route("/history", past),
            Route("/message", message, methods=["POST"]),
            Route("/feedback", give_feedback, methods=["POST"]),
            Route("/files/{name}", document),
        ],
        lifespan=lifespan,
    )


def serve() -> None:
    """`mentor local`: the page, inside the bot's container (published on 127.0.0.1 only)."""
    import uvicorn

    from mentor.agent.app import open_agent

    uvicorn.run(build(open_agent), host="0.0.0.0", port=PORT, log_level="info")  # noqa: S104
