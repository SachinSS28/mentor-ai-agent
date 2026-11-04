"""Creates Mentor's Google outbox in the owner's account, so the owner only clicks "Allow".

Through the Apps Script API, with a Google sign-in used for this alone (kept in memory, never
saved): create the project, upload infra/outbox/Code.gs with its manifest, deploy it as a web app,
open the web app once so the owner approves what the script may do (send to Telegram, run every
minute), then connect it (mentor.outbox.connect).
"""

import json
import subprocess
import time
from collections.abc import Callable
from typing import Any

import httpx
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mentor import outbox
from mentor.settings import PRIVATE_DIR, REPO_ROOT, get_settings

SCOPES = [
    "https://www.googleapis.com/auth/script.projects",
    "https://www.googleapis.com/auth/script.deployments",
]
CODE = REPO_ROOT / "infra" / "outbox" / "Code.gs"
# What the script may do once approved: call Telegram and keep its one-minute timer.
MANIFEST = {
    "timeZone": "Asia/Hong_Kong",
    "runtimeVersion": "V8",
    "exceptionLogging": "STACKDRIVER",
    "oauthScopes": [
        "https://www.googleapis.com/auth/script.external_request",
        "https://www.googleapis.com/auth/script.scriptapp",
    ],
    "webapp": {"executeAs": "USER_DEPLOYING", "access": "ANYONE_ANONYMOUS"},
}
USER_SETTINGS = "https://script.google.com/home/usersettings"
WAIT_SECONDS = 30 * 60
TITLE = "Mentor outbox"
SAVED = PRIVATE_DIR / "outbox_script.json"  # the script made earlier, so a rerun picks it up


def say(text: str) -> None:
    print(text, flush=True)


def open_in_safari(url: str) -> None:
    subprocess.run(["/usr/bin/open", "-a", "Safari", url], check=False)  # noqa: S603


def sign_in() -> Any:
    flow = InstalledAppFlow.from_client_secrets_file(
        str(get_settings().google_client_secret_path), SCOPES
    )
    return flow.run_local_server(
        port=0,
        open_browser=True,
        success_message="Done: you can close this tab and go back to Mentor.",
    )


def create_project(service: Any, pause: Callable[[float], None] = time.sleep) -> str:
    """Creates the project; if Google says the owner's Apps Script API switch is off, opens that
    setting and waits for it to be switched on."""
    deadline = time.monotonic() + WAIT_SECONDS
    asked = False
    while True:
        try:
            created = service.projects().create(body={"title": TITLE}).execute()
            return str(created["scriptId"])
        except HttpError as error:
            if "Apps Script API" not in str(error) or time.monotonic() > deadline:
                raise
            if not asked:
                say(
                    "   Google needs one switch: on the page that opens, turn on "
                    '"Google Apps Script API". I carry on by myself once it\'s on.'
                )
                open_in_safari(USER_SETTINGS)
                asked = True
            pause(10)


def deploy(service: Any, script_id: str) -> str:
    """Uploads the script, deploys it as a web app and returns the web app's address."""
    files = [
        {"name": "Code", "type": "SERVER_JS", "source": CODE.read_text()},
        {"name": "appsscript", "type": "JSON", "source": json.dumps(MANIFEST, indent=2)},
    ]
    projects = service.projects()
    projects.updateContent(scriptId=script_id, body={"files": files}).execute()
    version = projects.versions().create(scriptId=script_id, body={"description": TITLE}).execute()
    body = {
        "versionNumber": version["versionNumber"],
        "manifestFileName": "appsscript",
        "description": TITLE,
    }
    deployment = projects.deployments().create(scriptId=script_id, body=body).execute()
    for entry in deployment.get("entryPoints", []):
        if entry.get("entryPointType") == "WEB_APP":
            return str(entry["webApp"]["url"])
    raise outbox.OutboxError("Google deployed the script without a web app")


def web_app(service: Any, script_id: str) -> str | None:
    """The address of an existing web app deployment of the script, if there is one."""
    found = service.projects().deployments().list(scriptId=script_id).execute()
    for deployment in found.get("deployments", []):
        for entry in deployment.get("entryPoints", []):
            url = str(entry.get("webApp", {}).get("url", ""))
            if entry.get("entryPointType") == "WEB_APP" and url.endswith("/exec"):
                return url
    return None


def saved() -> dict[str, str]:
    try:
        found = json.loads(SAVED.read_text())
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def approved(url: str) -> bool:
    """Whether the owner has approved the script: it then answers in JSON (refusing a request
    without the secret); before that, Google answers with an authorization page."""
    try:
        response = httpx.post(
            url, json={"action": "status", "secret": ""}, follow_redirects=True, timeout=30
        )
        answer = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    return isinstance(answer, dict) and "ok" in answer


def wait_for_approval(url: str, pause: Callable[[float], None] = time.sleep) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while not approved(url):
        if time.monotonic() > deadline:
            minutes = WAIT_SECONDS // 60
            raise outbox.OutboxError(f"the script wasn't approved within {minutes} minutes")
        pause(5)


def setup() -> None:
    say(
        "1/4 A Google window opens in your browser: choose sachinsshende@gmail.com, and if Google "
        'says it hasn\'t verified the app, click Advanced, then "Go to …", then Allow.'
    )
    service = build("script", "v1", credentials=sign_in(), cache_discovery=False)
    script_id = saved().get("script_id", "")
    url = web_app(service, script_id) if script_id else None
    if url:
        say("2/4 Found the outbox script made earlier in your Google account.")
    else:
        say("2/4 Creating the outbox script in your Google account…")
        script_id = create_project(service)
        url = deploy(service, script_id)
    SAVED.write_text(json.dumps({"script_id": script_id, "url": url}))
    # Apps Script's own editor: the older /d/…/edit address goes through Drive, which can fail.
    page = f"https://script.google.com/home/projects/{script_id}/edit"
    say(f"   Script: {page}\n   Web app: {url}")
    if not approved(url):
        say(
            '3/4 Approve it: in the "Mentor outbox" editor that opens, click Run at the top, then '
            "Review permissions, choose your account, then Advanced, then "
            '"Go to Mentor outbox (unsafe)", then Allow.'
        )
        open_in_safari(page)
        wait_for_approval(url)
    say("4/4 Approved. Connecting it…")
    outbox.connect(url)
