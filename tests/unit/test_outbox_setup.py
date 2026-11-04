"""Creating the outbox through the Apps Script API, with Google stood in."""

import json
from types import SimpleNamespace
from typing import Any

import httplib2
import httpx
import pytest
from googleapiclient.errors import HttpError

from mentor import outbox_setup

URL = "https://script.google.com/macros/s/AKfycb-Mentor/exec"


class Call:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error

    def execute(self) -> Any:
        if self.error:
            raise self.error
        return self.result


def switched_off() -> HttpError:
    content = json.dumps(
        {"error": {"code": 403, "message": "User has not enabled the Apps Script API."}}
    ).encode()
    return HttpError(httplib2.Response({"status": 403}), content)


def fake_google(create_errors: int = 0) -> tuple[Any, list[tuple[str, dict[str, Any]]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    errors = [switched_off() for _ in range(create_errors)]

    def create(body: dict[str, Any]) -> Call:
        calls.append(("create", body))
        return Call(error=errors.pop()) if errors else Call({"scriptId": "S1"})

    def update(scriptId: str, body: dict[str, Any]) -> Call:  # noqa: N803 (Google's name)
        calls.append(("updateContent", body))
        return Call({})

    def version(scriptId: str, body: dict[str, Any]) -> Call:  # noqa: N803
        calls.append(("version", body))
        return Call({"versionNumber": 1})

    def deployment(scriptId: str, body: dict[str, Any]) -> Call:  # noqa: N803
        calls.append(("deployment", body))
        entry = {"entryPointType": "WEB_APP", "webApp": {"url": URL}}
        return Call({"entryPoints": [entry]})

    projects = SimpleNamespace(
        create=create,
        updateContent=update,
        versions=lambda: SimpleNamespace(create=version),
        deployments=lambda: SimpleNamespace(create=deployment),
    )
    return SimpleNamespace(projects=lambda: projects), calls


def test_the_script_is_uploaded_and_deployed_as_a_web_app() -> None:
    service, calls = fake_google()
    assert outbox_setup.create_project(service) == "S1"
    assert outbox_setup.deploy(service, "S1") == URL
    files = {f["name"]: f for f in calls[1][1]["files"]}
    assert "function deliver()" in files["Code"]["source"]
    manifest = json.loads(files["appsscript"]["source"])
    assert manifest["webapp"] == {"executeAs": "USER_DEPLOYING", "access": "ANYONE_ANONYMOUS"}
    assert manifest["oauthScopes"] == [  # Telegram and its timer, nothing else in the account
        "https://www.googleapis.com/auth/script.external_request",
        "https://www.googleapis.com/auth/script.scriptapp",
    ]
    assert calls[3] == (
        "deployment",
        {"versionNumber": 1, "manifestFileName": "appsscript", "description": "Mentor outbox"},
    )


def test_a_switched_off_apps_script_api_is_opened_and_waited_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(outbox_setup, "open_in_safari", opened.append)
    service, calls = fake_google(create_errors=2)
    assert outbox_setup.create_project(service, pause=lambda seconds: None) == "S1"
    assert opened == [outbox_setup.USER_SETTINGS]  # opened once, retried until it's on
    assert [name for name, _ in calls] == ["create", "create", "create"]


def test_approval_is_seen_when_the_script_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(
        [
            httpx.Response(200, text="<html>Authorization needed</html>"),
            httpx.Response(200, json={"ok": False, "error": "Not allowed."}),
        ]
    )

    def post(url: str, **kwargs: Any) -> httpx.Response:
        response = next(answers)
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setattr(outbox_setup.httpx, "post", post)
    assert outbox_setup.approved(URL) is False  # Google's approval page
    assert (
        outbox_setup.approved(URL) is True
    )  # the script itself, refusing a request without the secret
