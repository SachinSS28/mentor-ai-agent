"""LinkedIn for Mentor's tools and the morning search. Look-ups go to the gateway on the Mac
(`mentor linkedin`), which offers only LinkedIn's read tools and keeps the pace; this side only
turns its answers and failures into something Mentor can say in a line."""

from typing import Any

import anyio
from fastmcp import Client
from fastmcp.client.transports import ClientTransport
from fastmcp.exceptions import ToolError

from mentor.settings import get_settings

# A look-up may wait for the pause between look-ups (up to 40 s), then for slow pages.
TIMEOUT = 300


class LinkedInUnavailable(Exception):
    """LinkedIn couldn't be used; the message says why, in a line for the owner."""


def target() -> tuple[ClientTransport | str, str | None]:
    """Where the gateway is, and the token it expects."""
    settings = get_settings()
    try:
        token = settings.linkedin_token_path.read_text().strip()
    except OSError:
        raise LinkedInUnavailable(
            "LinkedIn isn't set up on the Mac yet (its LinkedIn service has never run)."
        ) from None
    return settings.linkedin_gateway_url, token


def call(tool: str, **arguments: Any) -> dict[str, Any]:
    """One LinkedIn look-up through the gateway. Tools run in worker threads, outside any event
    loop, so this runs its own."""
    where, token = target()
    args = {key: value for key, value in arguments.items() if value is not None}

    async def run() -> dict[str, Any]:
        async with Client(where, auth=token, timeout=TIMEOUT) as client:
            result = await client.call_tool(tool, args)
        data = result.structured_content or {}
        return dict(data.get("result", data)) if isinstance(data, dict) else {}

    try:
        return anyio.run(run)
    except ToolError as error:
        raise LinkedInUnavailable(str(error).removeprefix("Error calling tool ")) from None
    except Exception as error:  # connection refused, timeouts, a closed session
        raise LinkedInUnavailable(
            "LinkedIn is reachable only while the Mac is awake and its LinkedIn service runs "
            f"({type(error).__name__})."
        ) from None
