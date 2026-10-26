from collections.abc import Callable, Iterator

import pytest

from mentor.settings import get_settings


@pytest.fixture(autouse=True)
def no_real_linkedin(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """No test may reach the real LinkedIn gateway on this Mac: the token path points nowhere and
    the address at a closed port, so a test that forgets to stand LinkedIn in fails safe."""
    monkeypatch.setenv("MENTOR_LINKEDIN_TOKEN_PATH", str(tmp_path_factory.mktemp("li") / "none"))
    monkeypatch.setenv("MENTOR_LINKEDIN_GATEWAY_URL", "http://127.0.0.1:9/mcp")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def system_prompt() -> Iterator[Callable[[], str]]:
    yield lambda: "You are Mentor."
