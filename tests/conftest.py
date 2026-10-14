"""Shared test setup."""

from collections.abc import Iterator

import pytest

from edmultitool.plugin import ScoutPlugin


@pytest.fixture(autouse=True)
def _no_arrival_lookups(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every jump asks Spansh for the bodies of the new system. Tests of the other cards script their Spansh
    replies call by call, and the EDMC panel tests run with the real network, so the lookup stays off
    unless a test is marked `arrival` (tests/test_arrival.py)."""
    if request.node.get_closest_marker("arrival") is None:
        monkeypatch.setattr(ScoutPlugin, "_start_arrival", lambda self: None)
    yield


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "arrival: the jump-time body lookup stays on")
