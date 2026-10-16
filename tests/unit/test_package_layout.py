import importlib

import pytest

SUBPACKAGES = [
    "mentor.agent",
    "mentor.sentinel",
    "mentor.mcp_servers.mentor_core",
    "mentor.mcp_servers.google_tools",
    "mentor.channels.telegram",
    "mentor.api",
    "mentor.worker",
    "mentor.db",
]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    assert importlib.import_module(name).__doc__
