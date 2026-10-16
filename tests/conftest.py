from collections.abc import Callable, Iterator

import pytest


@pytest.fixture
def system_prompt() -> Iterator[Callable[[], str]]:
    yield lambda: "You are Mentor."
