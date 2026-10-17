"""Google OAuth for Mentor's own account access (G3: own thin tools).

Until AWS access is ready, the OAuth client and token live in a private folder outside the
repo (~/.config/mentor, owner-only permissions). From Phase 1 they move into Secrets Manager.
"""

import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from mentor.settings import get_settings

# Read-only for now; Phase 1 adds calendar holds and Gmail drafts.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]


SIGN_IN_EXPIRED = (
    "Google sign-in has expired (while the Google app is in Testing mode, Google ends it every "
    "7 days). Reconnect on the Mac in 30 seconds with: uv run mentor google-auth, then update "
    "the cloud server with: infra/gcp/deploy.sh google-token"
)


class GoogleNotConnectedError(RuntimeError):
    pass


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)


def connect_google() -> str:
    """Run the browser consent flow once and save the token. Returns the connected scopes."""
    settings = get_settings()
    if not settings.google_client_secret_path.exists():
        raise GoogleNotConnectedError(
            f"OAuth client file not found at {settings.google_client_secret_path}"
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.google_client_secret_path), SCOPES
    )
    creds = flow.run_local_server(port=0, open_browser=True)
    _write_private(settings.google_token_path, creds.to_json())
    return ", ".join(creds.scopes or [])


def get_credentials() -> Credentials:
    """Load the saved token, refreshing it when it has expired."""
    token_path = get_settings().google_token_path
    if not token_path.exists():
        raise GoogleNotConnectedError("Google isn't connected yet. Run: uv run mentor google-auth")
    creds: Credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            raise GoogleNotConnectedError("Google token is invalid. Run: uv run mentor google-auth")
        try:
            creds.refresh(Request())
        except RefreshError as error:
            raise GoogleNotConnectedError(SIGN_IN_EXPIRED) from error
        _write_private(token_path, creds.to_json())
    return creds
