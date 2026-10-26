"""LinkedIn: parsing what the server returns, the guard, the gateway and the bot's client, with a
fake LinkedIn server and no network or database."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import anyio
import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from tests.helpers_linkedin import POSTS, SEARCH_JOBS, SEARCH_PEOPLE

from mentor.charter import LinkedInConfig
from mentor.linkedin import client, gateway, guard, results

HKT = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 27, 10, 0, tzinfo=HKT)
OPEN = LinkedInConfig(enabled=True, start="00:00", end="23:59:59", min_gap_seconds=0)


def test_jobs_come_from_job_links_and_the_lines_after_them() -> None:
    first, second = results.jobs(SEARCH_JOBS, "LinkedIn search: wastewater")
    assert (first.external_id, first.title, first.company, first.location) == (
        "4100000101",
        "Senior Wastewater Engineer",
        "Harbour Water Consultants",
        "Hong Kong SAR (On-site)",
    )
    assert first.insights == ("Easy Apply", "3 connections work here")
    assert first.url == "https://www.linkedin.com/jobs/view/4100000101/"
    assert first.alert == "LinkedIn search: wastewater"
    assert (second.title, second.company, second.location) == (
        "Hydraulic Modeller",
        "Kowloon Engineering Ltd",
        "Kowloon, Hong Kong SAR",
    )
    assert second.insights == ("Actively reviewing applicants",)
    # 4100000103 had no title on the page, so it isn't stored without one.


def test_people_come_from_profile_links_and_the_lines_after_them() -> None:
    chan, wong = results.people(SEARCH_PEOPLE)
    assert (chan.username, chan.name, chan.headline, chan.location) == (
        "chan-tai-man-123",
        "Chan Tai Man",
        "Senior Engineer at Water Supplies Department",
        "Hong Kong SAR",
    )
    assert chan.organization == "Water Supplies Department"
    assert chan.url == "https://www.linkedin.com/in/chan-tai-man-123/"
    assert (wong.username, wong.name, wong.headline) == (
        "wongmeiling",
        "Wong Mei Ling",
        "Associate Professor, Civil Engineering",
    )
    assert (
        wong.organization is None
        and wong.about == "Associate Professor, Civil Engineering · Hong Kong"
    )


def test_links_and_page_text() -> None:
    assert results.job_id("https://www.linkedin.com/jobs/view/4100000101/?refId=x") == "4100000101"
    assert results.job_id("https://www.linkedin.com/jobs/view/senior-engineer-at-x-4100000101")
    assert results.job_id("4100000101") == "4100000101" and results.job_id("J12") is None
    assert results.profile_username("https://hk.linkedin.com/in/chan-tai-man-123?x=1") == (
        "chan-tai-man-123"
    )
    assert results.company_slug("https://www.linkedin.com/company/wsd/about/") == "wsd"
    assert results.post_links(POSTS) == ["https://www.linkedin.com/feed/update/urn:li:activity:1/"]
    long = {"sections": {"a": "line\n" * 500}}
    assert len(results.page_text(long, 100)) <= 102
    assert [results.date_posted(d) for d in (1, 7, 30, 90)] == [
        "past_24_hours",
        "past_week",
        "past_month",
        None,
    ]


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    saved: dict[str, str] = {}
    monkeypatch.setattr(guard, "get_state", saved.get)
    monkeypatch.setattr(guard, "set_state", saved.__setitem__)
    return saved


def test_only_reading_is_allowed(state: dict[str, str]) -> None:
    for tool in ("send_message", "connect_with_person", "get_inbox"):
        with pytest.raises(guard.Blocked, match="only reads LinkedIn"):
            guard.check(tool, NOW, OPEN)
    with pytest.raises(guard.Blocked, match="switched off in the charter"):
        guard.check("search_jobs", NOW, LinkedInConfig(enabled=False))
    assert guard.check("search_jobs", NOW, OPEN) == 0.0


def test_waking_hours_budget_and_pace(state: dict[str, str]) -> None:
    config = LinkedInConfig(enabled=True, daily_limit=3, hourly_limit=2, min_gap_seconds=20)
    with pytest.raises(guard.Blocked, match="only between 07:00 and 22:00"):
        guard.check("search_jobs", NOW.replace(hour=6, minute=59), config)

    guard.record(NOW, config, jitter=lambda: 0.5)  # next look-up 30 s later
    assert guard.check("search_jobs", NOW + timedelta(seconds=10), config) == 20.0
    guard.record(NOW + timedelta(minutes=1), config, jitter=lambda: 0.0)
    with pytest.raises(
        guard.Blocked, match="2 LinkedIn look-ups in the last hour; more from 11:00"
    ):
        guard.check("search_jobs", NOW + timedelta(minutes=5), config)

    guard.record(NOW + timedelta(hours=2), config)
    with pytest.raises(guard.Blocked, match="Today's 3 LinkedIn look-ups are used"):
        guard.check("search_jobs", NOW + timedelta(hours=4), config)
    assert guard.check("search_jobs", NOW + timedelta(days=1), config) == 0.0  # a new day


def test_pushback_is_recognised() -> None:
    assert guard.pushback("Rate limit detected. Wait 300 seconds before trying again.") == (
        "LinkedIn asked to slow down",
        True,
    )
    restricted = "LinkedIn has restricted access to this account and asks for verification."
    assert guard.pushback(restricted) == (
        "LinkedIn restricted the account and asks for identity verification",
        False,
    )
    assert guard.pushback("Session expired. Run with --login to create a new browser profile.")
    assert guard.pushback("Element not found: the experience section") is None
    blocked = {"section_errors": {"experience": {"error_type": "rate_limit"}}}
    assert guard.pushback_in(blocked) == ("LinkedIn asked to slow down", True)
    assert guard.pushback_in({"section_errors": {"x": {"error_type": "not_found"}}}) is None


def test_a_rate_limit_pauses_a_day_and_the_owner_hears_once(state: dict[str, str]) -> None:
    guard.stop(NOW, "LinkedIn asked to slow down", 24)
    with pytest.raises(guard.Blocked, match="wait until Wed 10:00"):
        guard.check("search_jobs", NOW + timedelta(hours=1), OPEN)
    assert "paused LinkedIn look-ups until Wed 10:00" in (guard.notice(NOW) or "")
    assert guard.notice(NOW) is None  # told once
    assert guard.check("search_jobs", NOW + timedelta(hours=25), OPEN) == 0.0  # over by itself


def test_a_sign_in_problem_stops_until_the_owner_says_so(state: dict[str, str]) -> None:
    guard.stop(NOW, "LinkedIn needs you to sign in again", None)
    with pytest.raises(guard.Blocked, match="Send /linkedin on once that's sorted"):
        guard.check("search_jobs", NOW + timedelta(days=3), OPEN)
    assert "mentor linkedin --login" in (guard.notice(NOW) or "")
    assert "stopped: LinkedIn needs you to sign in again" in guard.status(NOW, OPEN)
    guard.switch_on()
    assert guard.check("search_jobs", NOW, OPEN) == 0.0
    guard.switch_off(NOW)
    assert guard.notice(NOW) is None  # the owner did it, so nothing to tell
    assert "you switched LinkedIn off" in guard.status(NOW, OPEN)


def fake_linkedin() -> FastMCP:
    """Stands in for the LinkedIn MCP server, write tools included."""
    server = FastMCP("fake-linkedin")

    @server.tool
    def search_jobs(
        keywords: str,
        location: str | None = None,
        date_posted: str | None = None,
        max_pages: int = 3,
    ) -> dict[str, Any]:
        return {**SEARCH_JOBS, "asked": [keywords, location, date_posted, max_pages]}

    @server.tool
    def get_job_details(job_id: str) -> dict[str, Any]:
        raise ToolError("Rate limit detected. Wait 300 seconds before trying again.")

    @server.tool
    def get_person_profile(linkedin_username: str) -> dict[str, Any]:
        return {"sections": {"x": "?"}, "section_errors": {"x": {"error_type": "rate_limit"}}}

    @server.tool
    def send_message(linkedin_username: str, message: str) -> dict[str, Any]:
        raise AssertionError("the gateway must never offer this")

    return server


@pytest.fixture
def mentor_gateway(state: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    charter = SimpleNamespace(linkedin=OPEN)
    monkeypatch.setattr(gateway, "get_charter", lambda: charter)
    return gateway.build(gateway.Gateway(FastMCPTransport(fake_linkedin())))


def test_the_gateway_offers_only_reading(mentor_gateway: FastMCP) -> None:
    async def tools() -> set[str]:
        async with Client(mentor_gateway) as c:
            return {tool.name for tool in await c.list_tools()}

    assert anyio.run(tools) == guard.READ_TOOLS


def test_the_gateway_relays_counts_and_stops_on_pushback(
    mentor_gateway: FastMCP, state: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client, "target", lambda: (FastMCPTransport(mentor_gateway), None))
    found = client.call("search_jobs", keywords="wastewater", location="Hong Kong")
    assert found["asked"] == ["wastewater", "Hong Kong", None, 1]  # one page, always
    assert len(guard.recent(datetime.now(HKT))) == 1

    with pytest.raises(client.LinkedInUnavailable, match="LinkedIn pushed back"):
        client.call("get_job_details", job_id="4100000101")
    with pytest.raises(client.LinkedInUnavailable, match="LinkedIn is stopped: LinkedIn asked"):
        client.call("search_jobs", keywords="hydraulics")
    assert len(guard.recent(datetime.now(HKT))) == 2  # the blocked look-up never reached LinkedIn


def test_a_rate_limited_section_stops_look_ups_too(
    mentor_gateway: FastMCP, state: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client, "target", lambda: (FastMCPTransport(mentor_gateway), None))
    client.call("get_person_profile", linkedin_username="chan-tai-man-123")
    assert guard.stopped(datetime.now(HKT))


def test_the_gateway_wants_its_token(tmp_path: Path) -> None:
    def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = gateway.TokenCheck(Starlette(routes=[Route("/mcp", ok)]), token="s3cret")  # noqa: S106

    async def statuses() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            return [
                (await c.get("/mcp")).status_code,
                (await c.get("/mcp", headers={"Authorization": "Bearer wrong"})).status_code,
                (await c.get("/mcp", headers={"Authorization": "Bearer s3cret"})).status_code,
            ]

    assert anyio.run(statuses) == [401, 401, 200]
    token = gateway.gateway_token(tmp_path / "mentor" / "linkedin_gateway_token")
    assert gateway.gateway_token(tmp_path / "mentor" / "linkedin_gateway_token") == token
    assert (tmp_path / "mentor" / "linkedin_gateway_token").stat().st_mode & 0o777 == 0o600


def test_the_client_says_why_linkedin_is_out_of_reach(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = SimpleNamespace(
        linkedin_token_path=tmp_path / "missing", linkedin_gateway_url="http://127.0.0.1:9/mcp"
    )
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    with pytest.raises(client.LinkedInUnavailable, match="isn't set up on the Mac yet"):
        client.call("search_jobs", keywords="x")
    settings.linkedin_token_path.write_text("t")
    with pytest.raises(client.LinkedInUnavailable, match="only while the Mac is awake"):
        client.call("search_jobs", keywords="x")


def test_the_server_is_pinned_and_kept_from_other_browsers() -> None:
    assert gateway.SERVER == "mcp-server-linkedin==4.28.0"
    assert {"--no-auto-import", "--no-daemon"} <= set(gateway.SERVER_FLAGS)
    assert gateway.SERVER_ENV == {"LINKEDIN_MCP_CHECK_FOR_UPDATES": "off"}
