"""Standby on the Mac: keeps Mentor answering on Telegram while the cloud server is down.

Telegram lets one copy of a bot connect at a time. Normally the cloud's copy answers, and once a
minute the standby reads its heartbeat through an SSH tunnel (via Google's IAP, as deploy.sh
does). If the cloud stays down, the Mac starts its own copy of the bot (local model, same
memory) and says so in the chat. Once the cloud is reachable again, the standby copies the Mac's
side of the conversation over, hands back and says so. In between, it syncs conversations and
memories every few minutes, so whichever copy answers knows the same things.

It never takes over because of the Mac's own problems: while the Mac is offline or gcloud is
signed out it can't tell how the cloud is doing, so it waits.
"""

import logging
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Literal

import httpx
from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

from mentor.channels.telegram.bot import configure_logging
from mentor.cli import not_ready
from mentor.db.heartbeat import silence
from mentor.db.session import get_engine
from mentor.db.sync import sync
from mentor.settings import REPO_ROOT, Settings, get_settings

log = logging.getLogger(__name__)

CHECK_EVERY = 60  # seconds
SYNC_EVERY = timedelta(minutes=5)
# The cloud bot beats every 30 seconds, and a deploy restarts it in under a minute.
SILENCE_LIMIT = timedelta(minutes=3)
HAND_BACK_AFTER = 2  # checks in a row with the cloud reachable again
# If the cloud bot doesn't come back after a hand-back, the next try waits a while.
FAILED_HAND_BACK = timedelta(minutes=10)
RETRY_HAND_BACK = timedelta(minutes=30)

MAC_BOT = "com.mentor.telegram"
SERVICE_SCRIPT = REPO_ROOT / "infra" / "launchd" / "mentor-service.sh"
SERVER_DIR = "/opt/mentor"

TOOK_OVER = (
    "Heads-up: the cloud server isn't answering, so I'm running on your Mac for now (local "
    "model, so replies may be slower). I'll switch back by myself when the cloud is back."
)
HANDED_BACK = (
    "The cloud server is back, so I've switched back to it. Our conversation and my memory "
    "came along."
)


class Cloud(Enum):
    HEALTHY = "healthy"  # reachable, and its bot beat recently
    SILENT = "silent"  # reachable, but its bot isn't beating (stopped or stuck)
    STOPPED = "stopped"  # the server isn't running
    UNREACHABLE = "unreachable"  # the server runs, but the Mac can't reach its database
    UNKNOWN = "unknown"  # the Mac is offline or gcloud is signed out: can't tell


# Checks in a row (one a minute) before the Mac takes over, by how sure the standby is that the
# cloud's bot isn't answering. Silent means no heartbeat for SILENCE_LIMIT already; the second
# check gives a bot that was just (re)started time for its first one. Unreachable may be a
# passing network problem.
TAKE_OVER_AFTER = {Cloud.SILENT: 2, Cloud.STOPPED: 2, Cloud.UNREACHABLE: 10}

Action = Literal["wait", "take_over", "hand_back"]


@dataclass
class Standby:
    """Decides when to switch, from one check a minute. No side effects, so it's testable."""

    strikes: int = 0
    reachable: int = 0
    handed_back_at: datetime | None = None
    hand_back_from: datetime = datetime.min.replace(tzinfo=UTC)

    def decide(self, cloud: Cloud, mac_answering: bool, now: datetime) -> Action:
        if cloud is Cloud.UNKNOWN:
            return "wait"
        if not mac_answering:
            self.strikes = 0 if cloud is Cloud.HEALTHY else self.strikes + 1
            if cloud is not Cloud.HEALTHY and self.strikes >= TAKE_OVER_AFTER[cloud]:
                return "take_over"
            return "wait"
        if cloud is Cloud.HEALTHY:
            return "hand_back"  # both copies are connected (the server restarted its bot)
        self.reachable = self.reachable + 1 if cloud is Cloud.SILENT else 0
        if self.reachable >= HAND_BACK_AFTER and now >= self.hand_back_from:
            return "hand_back"
        return "wait"

    def took_over(self, now: datetime) -> None:
        failed = self.handed_back_at is not None and now - self.handed_back_at < FAILED_HAND_BACK
        self.hand_back_from = now + RETRY_HAND_BACK if failed else now
        self.strikes = self.reachable = 0

    def handed_back(self, now: datetime) -> None:
        self.handed_back_at = now
        self.strikes = self.reachable = 0


class CloudUnreachable(Exception):
    pass


def run(args: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    # Fixed commands built here, never from chat or email text.
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603


@dataclass
class CloudServer:
    gcloud: str
    project: str
    zone: str
    name: str
    password: str | None = None

    def compute(self, *command: str) -> list[str]:
        # Flags go right after the command: anything after "--" is passed on to ssh.
        return [self.gcloud, "compute", *command, "--project", self.project, "--zone", self.zone]

    def ssh_args(self) -> list[str]:
        return [*self.compute("ssh", self.name), "--tunnel-through-iap", "--quiet"]

    def ssh(self, command: str, timeout: int = 60) -> str:
        args = [*self.ssh_args(), "--command", command]
        try:
            result = run(args, timeout)
        except subprocess.TimeoutExpired:
            raise CloudUnreachable("SSH timed out") from None
        if result.returncode != 0:
            raise CloudUnreachable(f"SSH failed: {result.stderr.strip()[-200:]}")
        return result.stdout

    def status(self) -> str:
        """RUNNING, TERMINATED, ... or UNKNOWN if Google's API can't be asked."""
        args = [*self.compute("instances", "describe", self.name), "--format=value(status)"]
        try:
            result = run(args, 30)
        except subprocess.TimeoutExpired:
            return "UNKNOWN"
        return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"

    @contextmanager
    def database(self) -> Iterator[Engine]:
        """The server's Postgres, which listens only on the server, through an SSH tunnel."""
        if self.password is None:
            found = self.ssh(f"sudo sed -n 's/^POSTGRES_PASSWORD=//p' {SERVER_DIR}/.env").strip()
            if not found:
                raise CloudUnreachable("the server has no database password yet")
            self.password = found
        port = free_port()
        forward = ["-N", "-L", f"127.0.0.1:{port}:127.0.0.1:5432", "-o", "ExitOnForwardFailure=yes"]
        tunnel = subprocess.Popen(  # noqa: S603 (fixed command, see run())
            [*self.ssh_args(), "--", *forward], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            wait_for_port(port, tunnel)
            url = URL.create(
                "postgresql+psycopg", "mentor", self.password, "127.0.0.1", port, "mentor"
            )
            # One connection, reused: each new login through the tunnel costs a few seconds.
            engine = create_engine(url, pool_size=1, connect_args={"connect_timeout": 10})
            try:
                yield engine
            finally:
                engine.dispose()
        finally:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tunnel.kill()

    def stop_bot(self) -> None:
        self.ssh(f"cd {SERVER_DIR} && sudo docker compose stop telegram", timeout=90)

    def start_bot(self) -> None:
        self.ssh(f"cd {SERVER_DIR} && sudo docker compose up -d telegram", timeout=180)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def wait_for_port(port: int, tunnel: subprocess.Popen[Any], timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            raise CloudUnreachable("the SSH tunnel closed")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return
        except OSError:
            time.sleep(0.5)
    raise CloudUnreachable("the SSH tunnel didn't open in time")


def telegram(settings: Settings, method: str, **params: Any) -> bool:
    """Call the Bot API. Errors aren't logged: their text contains the bot token."""
    if settings.telegram_bot_token is None:
        return False
    token = settings.telegram_bot_token.get_secret_value()
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=15
        )
    except httpx.HTTPError:
        return False
    return response.is_success


def notify(settings: Settings, text: str) -> None:
    if not telegram(settings, "sendMessage", chat_id=settings.telegram_allowed_chat_id, text=text):
        log.warning("Couldn't send the Telegram message: %s", text)


def mac_answering() -> bool:
    return run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{MAC_BOT}"], 30).returncode == 0


def start_mac_bot() -> None:
    result = run([str(SERVICE_SCRIPT), "telegram"], 60)
    if result.returncode != 0:
        raise RuntimeError(f"Couldn't start the Mac's bot: {result.stderr.strip()}")


def stop_mac_bot() -> None:
    run([str(SERVICE_SCRIPT), "telegram", "uninstall"], 60)


def sync_with(cloud_db: Engine) -> bool:
    try:
        report = sync(get_engine(), cloud_db)
    except SQLAlchemyError as error:
        # The first line only: the rest repeats SQL parameters, i.e. conversation data.
        log.warning("Sync failed: %s", str(error).splitlines()[0])
        return False
    log.info("Synced with the cloud: %s", report)
    return True


def check(server: CloudServer, settings: Settings, sync_due: bool) -> tuple[Cloud, bool]:
    """How the cloud is doing, and whether a sync ran (if due) while connected."""
    if not telegram(settings, "getMe"):
        return Cloud.UNKNOWN, False  # the Mac itself is offline
    if run([server.gcloud, "auth", "print-access-token", "--quiet"], 30).returncode != 0:
        log.warning("gcloud is signed out, so the cloud can't be checked. Run: gcloud auth login")
        return Cloud.UNKNOWN, False
    try:
        with server.database() as cloud_db:
            quiet = silence(cloud_db, "cloud")
            synced = sync_with(cloud_db) if sync_due else False
    except (CloudUnreachable, SQLAlchemyError) as error:
        log.info("Can't reach the cloud's database: %s", str(error).splitlines()[0])
        state = server.status()
        stopped = state in ("TERMINATED", "STOPPED", "STOPPING", "SUSPENDED", "SUSPENDING")
        return (Cloud.STOPPED if stopped else Cloud.UNREACHABLE), False
    healthy = quiet is not None and quiet < SILENCE_LIMIT
    return (Cloud.HEALTHY if healthy else Cloud.SILENT), synced


def take_over(server: CloudServer, settings: Settings, cloud: Cloud) -> None:
    if cloud is not Cloud.STOPPED:
        # Make sure the cloud's copy is off (two copies would fight over messages), then bring
        # the Mac's memory up to date. Neither may work if the cloud can't be reached at all.
        try:
            server.stop_bot()
            with server.database() as cloud_db:
                sync_with(cloud_db)
        except (CloudUnreachable, SQLAlchemyError) as error:
            log.info("Couldn't reach the cloud before taking over: %s", error)
    start_mac_bot()
    log.info("Took over: the Mac's bot is answering (cloud %s)", cloud.value)
    notify(settings, TOOK_OVER)


def hand_back(server: CloudServer, settings: Settings) -> bool:
    try:
        with server.database() as cloud_db:
            server.stop_bot()  # the server may have restarted it by itself
            stop_mac_bot()
            sync_with(cloud_db)
            server.start_bot()
    except (CloudUnreachable, SQLAlchemyError) as error:
        log.warning("Hand-back failed, so the Mac keeps answering: %s", error)
        if not mac_answering():
            start_mac_bot()
        return False
    log.info("Handed back: the cloud's bot is answering")
    notify(settings, HANDED_BACK)
    return True


def cloud_server(settings: Settings) -> CloudServer:
    if settings.host != "mac":
        raise SystemExit("The standby runs on the Mac, not on the cloud server.")
    if not settings.gcp_project:
        raise SystemExit("Set MENTOR_GCP_PROJECT in .env to your Google Cloud project ID.")
    gcloud = shutil.which("gcloud")
    if gcloud is None:
        raise SystemExit("gcloud isn't installed or isn't on PATH.")
    return CloudServer(gcloud, settings.gcp_project, settings.gcp_zone, settings.gcp_server)


def sync_now() -> None:
    """`mentor sync`: one sync with the cloud, e.g. right before working on the Mac."""
    server = cloud_server(get_settings())
    try:
        with server.database() as cloud_db:
            print(f"Synced with the cloud server. {sync(get_engine(), cloud_db)}")
    except CloudUnreachable as error:
        raise SystemExit(f"Can't reach the cloud server: {error}") from None


def run_standby() -> None:
    configure_logging()
    settings = get_settings()
    server = cloud_server(settings)
    if settings.telegram_bot_token is None or settings.telegram_allowed_chat_id is None:
        raise SystemExit(
            "Set MENTOR_TELEGRAM_BOT_TOKEN and MENTOR_TELEGRAM_ALLOWED_CHAT_ID in .env"
        )
    log.info("Standby is watching the cloud server %s every %ds", server.name, CHECK_EVERY)

    standby = Standby()
    last_sync = datetime.min.replace(tzinfo=UTC)
    last_cloud: Cloud | None = None
    blocked: str | None = None  # why the Mac can't take over, once said
    while True:
        started = time.monotonic()
        now = datetime.now(UTC)
        mac_on = mac_answering()
        cloud, synced = check(
            server, settings, sync_due=not mac_on and now - last_sync >= SYNC_EVERY
        )
        if synced:
            last_sync = now
        if cloud is not last_cloud:
            log.info("Cloud is %s; the Mac's bot is %s", cloud.value, "on" if mac_on else "off")
            last_cloud = cloud

        action = standby.decide(cloud, mac_on, now)
        if action == "take_over":
            problem = not_ready()
            if problem is None:
                blocked = None
                take_over(server, settings, cloud)
                standby.took_over(now)
            elif problem != blocked:
                notify(
                    settings,
                    f"The cloud server isn't answering, and your Mac can't take over: {problem}",
                )
                blocked = problem
        elif action == "hand_back" and hand_back(server, settings):
            standby.handed_back(now)
            last_sync = now
        # A check takes 10-20 seconds through the tunnel; keep to one a minute.
        time.sleep(max(0.0, CHECK_EVERY - (time.monotonic() - started)))
