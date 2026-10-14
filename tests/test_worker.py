import logging
import threading
import time
from collections.abc import Iterator

import pytest

from edmultitool.sources.http import Cancelled, NetworkError, explain
from edmultitool.worker import INTERNAL_ERROR, JobResult, Worker

LOG = logging.getLogger("test.worker")


@pytest.fixture
def worker() -> Iterator[Worker]:
    w = Worker(LOG)
    w.start()
    yield w
    w.stop()


def _wait_for(w: Worker, count: int = 1, timeout: float = 5.0) -> list[JobResult]:
    got: list[JobResult] = []
    deadline = time.monotonic() + timeout
    while len(got) < count and time.monotonic() < deadline:
        got += w.drain()
        time.sleep(0.01)
    assert len(got) >= count, f"only {len(got)} of {count} results"
    return got


def _block_until_cancelled(cancel: threading.Event) -> str:
    if cancel.wait(10):
        raise Cancelled("cancelled")
    return "never cancelled"


def test_job_runs_off_the_main_thread_and_returns_its_value(worker: Worker) -> None:
    job_id = worker.submit("a", lambda _c: threading.current_thread().name)
    (r,) = _wait_for(worker)
    assert (r.job_id, r.name, r.ok, r.value) == (job_id, "a", True, "EDMultitool worker")


def test_failures_become_results_and_the_thread_survives(worker: Worker) -> None:
    def network(_c: threading.Event) -> None:
        raise NetworkError("no route to host")

    def bug(_c: threading.Event) -> None:
        raise KeyError("oops")

    worker.submit("net", network)
    worker.submit("bug", bug)
    worker.submit("ok", lambda _c: 42)
    net, crash, ok = _wait_for(worker, 3)
    assert not net.ok and net.error == explain(NetworkError("no route to host"))
    assert crash.error == INTERNAL_ERROR
    assert ok.value == 42 and worker.alive


def test_cancel_ends_a_running_job_within_a_second(worker: Worker) -> None:
    started = threading.Event()

    def job(cancel: threading.Event) -> str:
        started.set()
        return _block_until_cancelled(cancel)

    worker.submit("scout", job)
    assert started.wait(2)
    t0 = time.monotonic()
    assert worker.cancel("scout") == 1
    (r,) = _wait_for(worker)
    assert r.cancelled and not r.ok
    assert time.monotonic() - t0 < 1.0


def test_new_job_with_same_name_replaces_the_running_one(worker: Worker) -> None:
    started = threading.Event()

    def first(cancel: threading.Event) -> str:
        started.set()
        return _block_until_cancelled(cancel)

    worker.submit("scout", first)
    assert started.wait(2)
    worker.submit("other", lambda _c: "kept")
    worker.submit("scout", lambda _c: "second")
    results = _wait_for(worker, 3)
    assert [(r.name, r.cancelled, r.value) for r in results] == [
        ("scout", True, None),
        ("other", False, "kept"),
        ("scout", False, "second"),
    ]


def test_queued_job_cancelled_before_it_starts_never_runs(worker: Worker) -> None:
    started = threading.Event()
    ran: list[str] = []

    def blocker(cancel: threading.Event) -> str:
        started.set()
        return _block_until_cancelled(cancel)

    worker.submit("block", blocker)
    assert started.wait(2)
    worker.submit("queued", lambda _c: ran.append("queued"))
    worker.cancel()
    results = _wait_for(worker, 2)
    assert all(r.cancelled for r in results) and ran == []


def test_stop_cancels_and_joins_and_is_idempotent() -> None:
    w = Worker(LOG)
    w.start()
    w.submit("scout", _block_until_cancelled)
    time.sleep(0.05)
    t0 = time.monotonic()
    assert w.stop() is True
    assert time.monotonic() - t0 < 1.0
    assert not w.alive
    assert w.stop() is True
    with pytest.raises(RuntimeError):
        w.submit("late", lambda _c: None)


def test_stop_gives_up_on_a_job_that_ignores_cancel(caplog: pytest.LogCaptureFixture) -> None:
    w = Worker(LOG)
    w.start()
    release = threading.Event()
    w.submit("stuck", lambda _c: release.wait(5))  # e.g. a request in flight
    time.sleep(0.05)
    t0 = time.monotonic()
    assert w.stop(timeout=0.2) is False
    assert time.monotonic() - t0 < 1.0
    assert "still busy" in caplog.text
    release.set()
