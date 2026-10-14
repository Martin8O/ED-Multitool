"""One background thread for slow work (online lookups). No tkinter, no EDMC imports.

Jobs run one at a time, in order. Each job gets its own cancel event and must pass it to every wait
(the HTTP client does). Results — value, error or "cancelled" — land in `results`; the main thread
drains them with `drain()` from a tk `after` timer, so this thread never touches tk at all, not even
`event_generate` (which can hang EDMC when it races the shutdown sequence).

`submit` with an existing name cancels the earlier job of that name, so a new search replaces the old one.
`stop` cancels everything, waits briefly for the thread, and is safe to call more than once. A thread
still busy after the wait (a request in flight) is left to end on its own and the worker is not
restarted — `stop` is meant for shutdown.
"""

import itertools
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass

from edmultitool.sources.http import Cancelled, SourceError, explain

JobFn = Callable[[threading.Event], object]
STOP_TIMEOUT = 2.0
INTERNAL_ERROR = "internal error"  # JobResult.error of a job that crashed (a bug, not a source failure)


@dataclass(frozen=True)
class JobResult:
    job_id: int
    name: str
    value: object = None
    error: str | None = None  # human-readable; set when the job failed
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.cancelled


@dataclass
class _Job:
    job_id: int
    name: str
    fn: JobFn
    cancel: threading.Event


class Worker:
    def __init__(self, log: logging.Logger, *, thread_name: str = "EDMultitool worker") -> None:
        self.log = log
        self.thread_name = thread_name
        self.results: queue.Queue[JobResult] = queue.Queue()
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._lock = threading.Lock()
        self._live: dict[int, _Job] = {}  # submitted and not yet finished
        self._ids = itertools.count(1)
        self._thread: threading.Thread | None = None
        self._stopping = False

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping = False
        # Daemon: a request stuck in flight (bounded by its timeout) never blocks EDMC from exiting.
        self._thread = threading.Thread(target=self._run, name=self.thread_name, daemon=True)
        self._thread.start()

    def submit(self, name: str, fn: JobFn) -> int:
        """Queue a job; an unfinished job with the same name is cancelled. Returns the job id."""
        with self._lock:
            if self._stopping or self._thread is None:
                raise RuntimeError("worker is not running")
            for job in self._live.values():
                if job.name == name:
                    job.cancel.set()
            job = _Job(next(self._ids), name, fn, threading.Event())
            self._live[job.job_id] = job
            self._jobs.put(job)  # inside the lock: a racing stop() can never queue its marker first
        return job.job_id

    def cancel(self, name: str | None = None) -> int:
        """Cancel unfinished jobs (all, or those with `name`); returns how many were signalled."""
        with self._lock:
            hits = [j for j in self._live.values() if name is None or j.name == name]
        for job in hits:
            job.cancel.set()
        return len(hits)

    def drain(self) -> list[JobResult]:
        """Main thread: everything finished since the last drain."""
        out: list[JobResult] = []
        while True:
            try:
                out.append(self.results.get_nowait())
            except queue.Empty:
                return out

    def stop(self, timeout: float = STOP_TIMEOUT) -> bool:
        """Cancel all jobs and wait up to `timeout` s for the thread; True when it has ended."""
        thread = self._thread
        if thread is None:
            return True
        with self._lock:
            self._stopping = True
        self.cancel()
        self._jobs.put(None)
        thread.join(timeout)
        if thread.is_alive():
            self.log.warning("worker still busy after %.1f s; leaving it to finish on its own", timeout)
            return False
        self._thread = None
        return True

    # --- worker thread ---------------------------------------------------------------------------
    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            self.results.put(self._execute(job))
            with self._lock:
                self._live.pop(job.job_id, None)

    def _execute(self, job: _Job) -> JobResult:
        if job.cancel.is_set():
            return JobResult(job.job_id, job.name, cancelled=True)
        try:
            value = job.fn(job.cancel)
        except Cancelled:
            return JobResult(job.job_id, job.name, cancelled=True)
        except SourceError as exc:
            self.log.warning("job %s failed: %s", job.name, exc)  # the full text; the page gets the gist
            return JobResult(job.job_id, job.name, error=explain(exc))
        except Exception:  # a bug in a job must not kill the thread
            self.log.exception("job %s crashed", job.name)
            return JobResult(job.job_id, job.name, error=INTERNAL_ERROR)
        if job.cancel.is_set():  # finished, but nobody wants it any more
            return JobResult(job.job_id, job.name, cancelled=True)
        return JobResult(job.job_id, job.name, value=value)
