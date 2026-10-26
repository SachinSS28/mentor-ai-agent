"""Mentor's LinkedIn gateway, on the Mac (`mentor linkedin`, a login service).

It runs the pinned LinkedIn MCP server (stickerdaniel/linkedin-mcp-server) privately over stdio,
so that server listens on no port, and offers the bot only LinkedIn's read tools: over HTTP on
127.0.0.1, with a token, Host and Origin checks, one look-up at a time, each through the guard.
The server keeps its own browser profile, signed in once by the owner (`mentor linkedin --login`);
it never reads cookies from the owner's other browsers.
"""

import asyncio
import hmac
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from secrets import token_urlsafe
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.client.transports import ClientTransport, StdioTransport
from fastmcp.exceptions import ToolError
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mentor.charter import get_charter, owner_timezone
from mentor.linkedin import guard
from mentor.settings import get_settings

log = logging.getLogger(__name__)

# Reviewed for Mentor on 26 Oct 2026: no telemetry. Besides LinkedIn it contacts only PyPI (an
# update notice, switched off below), GitHub's issue search (a section name, after a failed page
# read) and Chrome for Testing (its browser, on first start). Updates only with the owner's OK.
SERVER = "mcp-server-linkedin==4.28.0"
# Never import cookies from the owner's browsers; no shared browser daemon listening locally.
SERVER_FLAGS = ["--no-auto-import", "--no-daemon"]
SERVER_ENV = {"LINKEDIN_MCP_CHECK_FOR_UPDATES": "off"}
PASSED_ENV = ("PATH", "HOME", "TMPDIR", "USER", "LANG")
HOST, PORT = "127.0.0.1", 8765
CONTAINER_HOST = "host.docker.internal"  # how the bot's container reaches the Mac
UPSTREAM_TIMEOUT = 240  # seconds for one look-up, browser start included
LOG_DIR = Path.home() / "Library" / "Logs" / "Mentor"


def uvx() -> str:
    found = shutil.which("uvx")
    if not found:
        raise SystemExit("uvx isn't on the PATH: install uv (brew install uv).")
    return found


def server_env() -> dict[str, str]:
    return {key: os.environ[key] for key in PASSED_ENV if key in os.environ} | SERVER_ENV


def upstream() -> StdioTransport:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return StdioTransport(
        uvx(),
        [SERVER, *SERVER_FLAGS],
        env=server_env(),
        keep_alive=True,
        log_file=LOG_DIR / "linkedin-server.log",
    )


class Gateway:
    """Relays allowed look-ups to the LinkedIn server, one at a time, through the guard."""

    def __init__(self, transport: ClientTransport) -> None:
        self.upstream = Client(transport, timeout=UPSTREAM_TIMEOUT)
        self.lock = asyncio.Lock()

    async def relay(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        args = {key: value for key, value in arguments.items() if value is not None}
        async with self.lock:
            config = get_charter().linkedin
            try:
                wait = guard.check(tool, datetime.now(owner_timezone()), config)
            except guard.Blocked as blocked:
                raise ToolError(str(blocked)) from None
            if wait:
                await asyncio.sleep(wait)
            started = datetime.now(owner_timezone())
            guard.record(started, config)  # counted before the call: a failure still costs one
            log.info("LinkedIn look-up: %s", tool)
            try:
                async with self.upstream:
                    result = await self.upstream.call_tool(tool, args, raise_on_error=False)
            except Exception as error:
                log.warning("The LinkedIn server failed on %s: %r", tool, error)
                raise ToolError(
                    f"the LinkedIn server failed ({type(error).__name__}); try again later"
                ) from None
            text = " ".join(getattr(block, "text", "") for block in result.content)
            if result.is_error:
                if found := guard.pushback(text):
                    self.stop(started, *found)
                    raise ToolError(f"LinkedIn pushed back ({found[0]}), so look-ups are stopped")
                raise ToolError(f"LinkedIn couldn't read that: {text[:300]}")
            data = result.structured_content or {}
            if found := guard.pushback_in(data):
                self.stop(started, *found)
            return data

    @staticmethod
    def stop(now: datetime, reason: str, waiting_fixes: bool) -> None:
        hours = get_charter().linkedin.pause_hours if waiting_fixes else None
        guard.stop(now, reason, hours)
        log.warning("LinkedIn look-ups stopped: %s", reason)


def build(gateway: Gateway) -> FastMCP:
    """The gateway's tools: LinkedIn's read tools, with only the arguments Mentor uses."""
    mcp = FastMCP("mentor-linkedin", instructions="Read-only LinkedIn look-ups for Mentor.")

    @mcp.tool
    async def search_jobs(
        keywords: str, location: str | None = None, date_posted: str | None = None
    ) -> dict[str, Any]:
        """Search LinkedIn jobs: one page of results."""
        args = {"keywords": keywords, "location": location, "date_posted": date_posted}
        return await gateway.relay("search_jobs", {**args, "max_pages": 1})

    @mcp.tool
    async def get_job_details(job_id: str) -> dict[str, Any]:
        """A LinkedIn job posting in full."""
        return await gateway.relay("get_job_details", {"job_id": job_id})

    @mcp.tool
    async def search_people(keywords: str, location: str | None = None) -> dict[str, Any]:
        """Search LinkedIn people: one page of results."""
        return await gateway.relay("search_people", {"keywords": keywords, "location": location})

    @mcp.tool
    async def get_person_profile(linkedin_username: str) -> dict[str, Any]:
        """A person's LinkedIn profile: the main page only."""
        return await gateway.relay("get_person_profile", {"linkedin_username": linkedin_username})

    @mcp.tool
    async def get_company_profile(company_name: str) -> dict[str, Any]:
        """A company's LinkedIn page."""
        return await gateway.relay("get_company_profile", {"company_name": company_name})

    @mcp.tool
    async def get_company_posts(company_name: str) -> dict[str, Any]:
        """A company's recent LinkedIn posts."""
        args = {"company_name": company_name, "max_scrolls": 2}
        return await gateway.relay("get_company_posts", args)

    @mcp.tool
    async def search_posts(keywords: str, date_posted: str | None = None) -> dict[str, Any]:
        """Search LinkedIn posts: one page of results."""
        args = {"keywords": keywords, "date_posted": date_posted, "max_pages": 1}
        return await gateway.relay("search_posts", args)

    return mcp


class TokenCheck:
    """Only requests carrying the gateway's token get through, so nothing else on the Mac can use
    LinkedIn through it."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            sent = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(sent, self.expected):
                await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def gateway_token(path: Path) -> str:
    """The token the bot sends, made on first start, readable only by the owner."""
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(token_urlsafe(32))
    return path.read_text().strip()


def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = gateway_token(get_settings().linkedin_token_path)
    mcp = build(Gateway(upstream()))
    log.info("LinkedIn gateway on http://%s:%d/mcp, read-only (%s)", HOST, PORT, SERVER)
    mcp.run(
        transport="http",
        host=HOST,
        port=PORT,
        path="/mcp",
        show_banner=False,
        host_origin_protection=True,
        allowed_hosts=[CONTAINER_HOST],
        middleware=[Middleware(TokenCheck, token=token)],
    )


def login() -> None:
    """The one-time sign-in: LinkedIn opens in the server's own browser window, and the owner
    types their password and code there."""
    print("A LinkedIn window opens: sign in there (your password never passes through Mentor).")
    subprocess.run(  # noqa: S603 (the pinned server, fixed arguments)
        [uvx(), SERVER, "--login", "--no-auto-import"], env=server_env(), check=False
    )
