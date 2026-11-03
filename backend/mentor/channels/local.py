"""Mentor on this Mac: a chat page at http://localhost:8790 that works without internet.

Telegram needs the internet to carry messages; Mentor's own thinking (Gemma on Ollama's MLX
engine), tasks, memories, contacts and documents don't. This page talks to the same agent in the
same conversation as Telegram, so the two continue each other. It loads nothing from the
internet, and only the owner's browser on this Mac can use it: requests must name this Mac as
the host, and messages must come from the page itself (its origin), so no web page can send
Mentor instructions through it.
"""

import asyncio
import json
import re
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from mentor import checkin
from mentor.agent.graph import MentorGraph, ask
from mentor.channels.telegram.formatting import to_telegram_html
from mentor.charter import owner_timezone
from mentor.db.state import set_state
from mentor.settings import get_settings

PORT = 8790
ALLOWED_HOSTS = (f"localhost:{PORT}", f"127.0.0.1:{PORT}")
ALLOWED_ORIGINS = tuple(f"http://{host}" for host in ALLOWED_HOSTS)
STAMP = re.compile(r"^\[[^\]\n]{6,40}\]\s*")  # "[Mon 26 Oct 2026, 13:51] "
HISTORY = 30  # messages shown when the page opens
MAX_TEXT = 4000

# Added to a message sent while the Mac has no internet, so Mentor answers from what works.
OFFLINE_SUFFIX = (
    "\n\n(Sent while the Mac has no internet: email, calendar, web search, LinkedIn and news "
    "can't be reached; tasks, memories, contacts and documents can.)"
)
OFFLINE_NOTE = (
    "No internet: I'm answering from this Mac. Your tasks, memories, contacts, documents and "
    "notes work; email, calendar, web search, LinkedIn and news wait for the internet."
)


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


def history(graph: MentorGraph, limit: int = HISTORY) -> list[dict[str, str]]:
    """The conversation's latest messages, as the owner saw them (no tool traffic)."""
    state = graph.get_state({"configurable": {"thread_id": thread_id()}})
    shown = []
    for message in (state.values or {}).get("messages", []):
        if isinstance(message, HumanMessage):
            words = STAMP.sub("", message.text).removesuffix(OFFLINE_SUFFIX)
            shown.append({"who": "you", "html": to_telegram_html(words)})
        elif isinstance(message, AIMessage) and not message.tool_calls and message.text.strip():
            shown.append({"who": "mentor", "html": to_telegram_html(message.text)})
    return shown[-limit:]


def trusted(request: Request) -> bool:
    """This Mac's own page: the host is localhost, and a message comes from the page itself."""
    if request.headers.get("host", "") not in ALLOWED_HOSTS:
        return False
    if request.method == "POST":
        same_origin = request.headers.get("origin", "") in ALLOWED_ORIGINS
        is_json = request.headers.get("content-type", "").startswith("application/json")
        return same_origin and is_json
    return True


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
        try:
            text = str((await request.json()).get("text", "")).strip()[:MAX_TEXT]
        except (json.JSONDecodeError, AttributeError):
            text = ""
        if not text:
            return JSONResponse({"error": "Write a message first."}, 400)
        async with lock:
            # Check-ins stay quiet while the owner is chatting, here as in Telegram.
            now = datetime.now(owner_timezone())
            await asyncio.to_thread(set_state, checkin.OWNER_LAST_MESSAGE, now.isoformat())
            if not await asyncio.to_thread(online):
                text += OFFLINE_SUFFIX
            reply = await asyncio.to_thread(ask, agent["graph"], text, thread_id(), now)
        return JSONResponse({"html": to_telegram_html(reply)})

    return Starlette(
        routes=[
            Route("/", page),
            Route("/history", past),
            Route("/message", message, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


def serve() -> None:
    """`mentor local`: the page, inside the bot's container (published on 127.0.0.1 only)."""
    import uvicorn

    from mentor.agent.app import open_agent

    uvicorn.run(build(open_agent), host="0.0.0.0", port=PORT, log_level="info")  # noqa: S104


PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mentor</title>
<style>
:root { --bg: #f6f5f2; --card: #fff; --ink: #1d1d1f; --muted: #6e6e73; --me: #2f5d50;
  --me-ink: #fff; --line: #e3e1dc; --warn: #8a5a00; --warn-bg: #fff4dc; }
@media (prefers-color-scheme: dark) { :root { --bg: #161617; --card: #232325; --ink: #f2f2f2;
  --muted: #a1a1a6; --me: #3f7d6c; --line: #333336; --warn: #ffd38a; --warn-bg: #3a2e14; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
  display: flex; flex-direction: column; height: 100vh; }
header { padding: 14px 16px; border-bottom: 1px solid var(--line); display: flex;
  align-items: baseline; gap: 10px; }
header h1 { font-size: 18px; margin: 0; }
#state { color: var(--muted); font-size: 13px; }
#note { display: none; margin: 10px 16px 0; padding: 10px 12px; border-radius: 10px;
  background: var(--warn-bg); color: var(--warn); font-size: 14px; }
#log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.msg { max-width: min(720px, 88%); padding: 10px 14px; border-radius: 14px; white-space: pre-wrap;
  overflow-wrap: anywhere; }
.mentor { background: var(--card); border: 1px solid var(--line); align-self: flex-start; }
.you { background: var(--me); color: var(--me-ink); align-self: flex-end; }
.you a { color: var(--me-ink); }
.thinking { color: var(--muted); font-style: italic; }
form { display: flex; gap: 8px; padding: 12px 16px 16px; border-top: 1px solid var(--line); }
textarea { flex: 1; resize: none; height: 48px; padding: 12px; border-radius: 12px;
  border: 1px solid var(--line); background: var(--card); color: var(--ink); font: inherit; }
button { padding: 0 18px; border: 0; border-radius: 12px; background: var(--me); color: #fff;
  font: inherit; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
</style></head><body>
<header><h1>Mentor</h1><span id="state">on this Mac</span></header>
<div id="note"></div>
<div id="log"></div>
<form id="form"><textarea id="text" placeholder="Write to Mentor…" autofocus></textarea>
<button id="send" type="submit">Send</button></form>
<script>
const log = document.getElementById("log"), text = document.getElementById("text");
const send = document.getElementById("send"), note = document.getElementById("note");
function add(who, html, extra) {
  const div = document.createElement("div");
  div.className = "msg " + who + (extra ? " " + extra : "");
  div.innerHTML = html;  // the server escapes text and allows only bold, italic, code and links
  div.querySelectorAll("a").forEach(a => { a.target = "_blank"; a.rel = "noopener"; });
  log.appendChild(div); log.scrollTop = log.scrollHeight; return div;
}
function escape(s) {
  const d = document.createElement("div"); d.textContent = s; return d.innerHTML;
}
function status(online) {
  document.getElementById("state").textContent = online ? "on this Mac · online" :
    "on this Mac · no internet";
  note.style.display = online ? "none" : "block";
  note.textContent = online ? "" : __OFFLINE_NOTE__;
}
async function load() {
  const r = await fetch("/history"); const d = await r.json();
  d.messages.forEach(m => add(m.who, m.html)); status(d.online);
}
document.getElementById("form").addEventListener("submit", async e => {
  e.preventDefault();
  const words = text.value.trim(); if (!words) return;
  add("you", escape(words)); text.value = ""; send.disabled = true;
  const wait = add("mentor", "Thinking…", "thinking");
  try {
    const r = await fetch("/message", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: words }) });
    const d = await r.json(); wait.remove();
    add("mentor", d.html || escape(d.error || "No answer."));
  } catch (err) { wait.remove(); add("mentor", "Mentor isn't answering on this Mac right now."); }
  send.disabled = false; text.focus();
});
text.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send.click(); }
});
load();
</script></body></html>
"""


PAGE = PAGE_TEMPLATE.replace("__OFFLINE_NOTE__", json.dumps(OFFLINE_NOTE))
