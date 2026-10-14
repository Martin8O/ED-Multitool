"""Test doubles for the sources layer: scripted transport, fake clock, recorded Spansh replies."""

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from edmultitool.sources.http import Cancelled, NetworkError, PoliteClient, Request, Response

SPANSH_DATA = Path(__file__).parent / "data" / "spansh"


def recorded(name: str) -> dict[str, Any]:
    """A trimmed real Spansh exchange: {"request": {...}, "status": int, "body": {...}}."""
    doc: dict[str, Any] = json.loads((SPANSH_DATA / f"{name}.json").read_text(encoding="utf-8"))
    return doc


def reply(name: str) -> Response:
    doc = recorded(name)
    return Response(doc["status"], json.dumps(doc["body"]).encode("utf-8"))


class FakeTime:
    """Monotonic + wall clock that only moves when the client waits."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.waits: list[float] = []

    def clock(self) -> float:
        return self.now

    def wait(self, cancel: threading.Event, seconds: float) -> None:
        if cancel.is_set():
            raise Cancelled("cancelled")
        self.waits.append(round(seconds, 6))
        self.now += seconds


Step = Response | Exception | Callable[[Request], Response]


class ScriptedTransport:
    """Answers each call with the next scripted step; a step may be a Response, an exception to raise,
    or a function of the request. Runs out → AssertionError (an unexpected extra call)."""

    def __init__(self, *steps: Step, repeat_last: bool = False) -> None:
        self.steps = list(steps)
        self.repeat_last = repeat_last
        self.requests: list[Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Request, timeout: float) -> Response:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self.steps:
            raise AssertionError(f"unexpected call #{len(self.requests)}: {request.method} {request.url}")
        step = self.steps[0] if self.repeat_last and len(self.steps) == 1 else self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, Response):
            return step
        return step(request)


def network_down() -> NetworkError:
    return NetworkError("connection refused")


def make_client(
    transport: ScriptedTransport, fake: FakeTime, cache_dir: Path | None = None, **kw: Any
) -> PoliteClient:
    return PoliteClient(
        transport,
        user_agent="EDMultitool-test/1",
        cache_dir=cache_dir,
        clock=fake.clock,
        wall=fake.clock,
        wait=fake.wait,
        **kw,
    )
