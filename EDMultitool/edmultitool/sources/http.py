"""The one polite HTTP client every outbound call goes through.

- honest User-Agent, timeout on every request;
- at most one request per `min_interval` seconds (job polling therefore runs at ≤ 1/s);
- retry on 429 / 5xx / network failure: honours Retry-After, else 2 s, 4 s, 8 s; then gives up.
  A Retry-After longer than MAX_DELAY means "not now": give up at once rather than knock early.
  POSTs (job submits) are retried only on 429 / 503, where the server surely did not take the job —
  a timeout or a 502 may come after Spansh accepted it, and a retry would start a duplicate job;
- disk cache with a TTL, written atomically (survives EDMC restarts);
- every wait is a `cancel.wait()`, so cancelling interrupts it at once. A request already in flight
  finishes (bounded by the timeout) before the cancel is seen.

The network itself is a `Transport` (a callable), so tests replay recorded replies and never go online.
The default transport uses `requests`, which EDMC bundles with an up-to-date certificate store.
"""

import datetime
import email.utils
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_TIMEOUT = 20.0
MIN_INTERVAL = 1.0
MAX_RETRIES = 3
BACKOFF_BASE = 2.0
MAX_DELAY = 60.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
POST_RETRY_STATUSES = frozenset({429, 503})


class SourceError(Exception):
    """Anything that stops a source call from producing a result."""


class Cancelled(SourceError):
    pass


class NetworkError(SourceError):
    """No HTTP answer at all: DNS, connection, TLS or timeout."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


class HttpError(SourceError):
    """An HTTP error status. `said` is the service's own explanation when its reply carried one;
    `wait` the Retry-After that was too long to honour."""

    def __init__(self, status: int, message: str, *, said: str = "", wait: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.said = said
        self.wait = wait


class BadReply(SourceError):
    """The service answered, but not in the shape we rely on."""


@dataclass(frozen=True)
class Request:
    method: str  # "GET" / "POST"
    url: str
    params: Sequence[tuple[str, str]] = ()  # query string
    form: Sequence[tuple[str, str]] | None = None  # form-encoded body (POST)
    json_body: str | None = None  # JSON body (POST), already encoded
    idempotent: bool = False  # a POST that changes nothing (a search): retried like a GET
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)  # lower-case names
    from_cache: bool = False

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise BadReply(f"reply is not JSON (HTTP {self.status})") from exc


class Transport(Protocol):
    def __call__(self, request: Request, timeout: float) -> Response: ...


def requests_transport() -> Transport:
    """The real network, via one `requests` session per calling thread: the worker and the page's request
    threads (name suggestions) send at the same time, and a Session is not documented as safe to share
    between threads. Only the worker's session lives long enough to keep connections alive; the server
    starts a new thread per request, so each suggestion opens a fresh connection (cached replies skip it)."""
    import requests  # bundled with EDMC; imported here so tests with a fake transport never need it

    local = threading.local()

    def send(request: Request, timeout: float) -> Response:
        session = getattr(local, "session", None)
        if session is None:
            session = local.session = requests.Session()
        try:
            r = session.request(
                request.method,
                request.url,
                params=list(request.params) or None,
                data=(
                    request.json_body.encode("utf-8")
                    if request.json_body is not None
                    else list(request.form)
                    if request.form is not None
                    else None
                ),
                headers=dict(request.headers),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            timed_out = isinstance(exc, requests.Timeout)
            raise NetworkError(f"{request.method} {request.url}: {exc}", timed_out=timed_out) from exc
        return Response(r.status_code, r.content, {k.lower(): v for k, v in r.headers.items()})

    return send


def _wait(cancel: threading.Event, seconds: float) -> None:
    if seconds > 0 and cancel.wait(seconds):
        raise Cancelled("cancelled")


class PoliteClient:
    def __init__(
        self,
        transport: Transport,
        *,
        user_agent: str,
        cache_dir: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        min_interval: float = MIN_INTERVAL,
        max_retries: int = MAX_RETRIES,
        log: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        wait: Callable[[threading.Event, float], None] = _wait,
    ) -> None:
        self.transport = transport
        self.user_agent = user_agent
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.log = log or logging.getLogger(__name__)
        self._clock = clock
        self._wall = wall
        self._wait = wait
        self._pace_lock = threading.Lock()
        self._next_send = 0.0
        self._cache_failed = False

    # --- requests --------------------------------------------------------------------------------
    def get(
        self,
        url: str,
        cancel: threading.Event,
        *,
        params: Sequence[tuple[str, str]] = (),
        ttl: float | None = None,
        valid: Callable[[Any], bool] | None = None,
    ) -> Response:
        """GET; with `ttl`, a cached 200 reply younger than `ttl` seconds is returned without a call.
        Only a reply that is JSON and passes `valid` (when given) is remembered or served from the cache:
        a 200 maintenance page or a broken reply must not stand in for the real answer until the TTL ends."""
        key = None
        if ttl is not None:
            key = "GET " + url + "?" + "&".join(f"{k}={v}" for k, v in params)
            cached = self.cache_get(key, ttl)
            if cached is not None and _usable(cached.encode("utf-8"), valid):
                return Response(200, cached.encode("utf-8"), from_cache=True)
        resp = self.send(Request("GET", url, params=params), cancel)
        if key is not None and resp.status == 200 and _usable(resp.body, valid):
            self.cache_put(key, resp.body.decode("utf-8", errors="replace"))
        return resp

    def post_form(self, url: str, form: Sequence[tuple[str, str]], cancel: threading.Event) -> Response:
        return self.send(Request("POST", url, form=form), cancel)

    def search_json(self, url: str, doc: object, cancel: threading.Event) -> Response:
        """POST a JSON search: it changes nothing at the server, so it is retried like a GET."""
        body = json.dumps(doc, ensure_ascii=False)
        headers = {"Content-Type": "application/json"}
        return self.send(Request("POST", url, json_body=body, idempotent=True, headers=headers), cancel)

    def send(self, request: Request, cancel: threading.Event) -> Response:
        """Paced send with retries. Returns the final response, whatever its status. Raises when the
        retries run out, when the server asks for a wait longer than MAX_DELAY, or on a network
        failure of a POST that is not `idempotent` (a job submit is never retried after a network error)."""
        request = Request(
            request.method,
            request.url,
            request.params,
            request.form,
            request.json_body,
            request.idempotent,
            {"User-Agent": self.user_agent, "Accept": "application/json", **request.headers},
        )
        safe = request.method == "GET" or request.idempotent  # a job submit is never sent twice
        retryable = RETRY_STATUSES if safe else POST_RETRY_STATUSES
        attempt = 0
        while True:
            self._pace(cancel)
            if cancel.is_set():
                raise Cancelled("cancelled")
            retry_after: float | None = None
            failure: SourceError
            try:
                resp = self.transport(request, self.timeout)
            except NetworkError as exc:
                if not safe:
                    raise
                failure = exc
            else:
                if resp.status not in retryable:
                    return resp
                failure = HttpError(resp.status, f"{request.method} {request.url}: HTTP {resp.status}")
                retry_after = _retry_after(resp.headers.get("retry-after"), self._wall())
                if retry_after is not None and retry_after > MAX_DELAY:
                    message = f"{failure}; the server asks to wait {retry_after:.0f} s"
                    raise HttpError(resp.status, message, wait=retry_after)
            attempt += 1
            if attempt > self.max_retries:
                raise failure
            delay = retry_after if retry_after is not None else BACKOFF_BASE * 2 ** (attempt - 1)
            self.log.info("%s; retry %d/%d in %.0f s", failure, attempt, self.max_retries, delay)
            self._wait(cancel, delay)

    def _pace(self, cancel: threading.Event) -> None:
        """Hold each send until `min_interval` has passed since the previous one."""
        with self._pace_lock:
            delay = self._next_send - self._clock()
            if delay > 0:
                self._wait(cancel, delay)
            self._next_send = self._clock() + self.min_interval

    # --- disk cache ------------------------------------------------------------------------------
    def _cache_path(self, key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    def cache_get(self, key: str, ttl: float) -> str | None:
        path = self._cache_path(key)
        if path is None:
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            self._cache_warning(f"unreadable cache file {path.name}: {exc}")
            return None
        if not isinstance(doc, dict) or doc.get("key") != key or not isinstance(doc.get("body"), str):
            return None
        stored = doc.get("stored")
        if not isinstance(stored, int | float) or not 0 <= self._wall() - stored < ttl:
            return None
        body: str = doc["body"]
        return body

    def cache_put(self, key: str, body: str) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.stem}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"key": key, "stored": self._wall(), "body": body}), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            self._cache_warning(f"could not write cache file {path.name}: {exc}")

    def prune_cache(self, max_age: float) -> int:
        """Delete cache files older than `max_age` seconds; returns how many were removed."""
        if self.cache_dir is None or not self.cache_dir.is_dir():
            return 0
        removed = 0
        now = self._wall()
        for path in self.cache_dir.iterdir():
            if path.suffix not in (".json", ".tmp"):
                continue
            try:
                if now - path.stat().st_mtime > max_age:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                self._cache_warning(f"could not prune {path.name}: {exc}")
        return removed

    def _cache_warning(self, message: str) -> None:
        if not self._cache_failed:  # once per run: a broken cache only costs speed
            self.log.warning("%s (further cache problems are not logged)", message)
        self._cache_failed = True


def explain(exc: SourceError) -> str:
    """The failure in player words, without URLs or library text (those go to the log): a reason the
    caller puts after the service's name ("Spansh: …"). Passing trouble ends in what to do about it,
    after a "; " — `brief` drops that part where the advice does not fit."""
    if isinstance(exc, NetworkError):
        if exc.timed_out:
            return "no answer in time; try again in a minute"
        return "could not connect (no internet, or the site is down); try again in a minute"
    if isinstance(exc, HttpError):
        if exc.said:
            return exc.said
        if exc.wait is not None:
            wait = f"{exc.wait / 60:.0f} min" if exc.wait >= 90 else f"{exc.wait:.0f} s"
            return f"too busy, it asks to wait {wait}; try again then"
        if exc.status == 429:
            return "too many requests just now; try again in a minute"
        if exc.status >= 500:
            return f"the site is down or overloaded (HTTP {exc.status}); try again later"
        return f"refused the request (HTTP {exc.status})"
    if isinstance(exc, BadReply):
        return "sent a reply ED Multitool cannot read (maintenance?); try again later"
    return str(exc) or type(exc).__name__


def brief(reason: str) -> str:
    """`explain`'s reason without its advice: "no answer in time; try again…" → "no answer in time"."""
    return reason.partition("; try again")[0]


def _usable(body: bytes, valid: Callable[[Any], bool] | None) -> bool:
    """True when `body` is JSON that `valid` (if any) accepts; a validator raising SourceError rejects it."""
    try:
        doc = json.loads(body)
    except ValueError:
        return False
    if valid is None:
        return True
    try:
        return valid(doc)
    except SourceError:
        return False


def _retry_after(value: str | None, now: float) -> float | None:
    """Retry-After as seconds from now: either a number of seconds or an HTTP date."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:  # "-0000" parses as naive; HTTP dates are always UTC
            when = when.replace(tzinfo=datetime.UTC)
        seconds = when.timestamp() - now
        seconds = max(seconds, 0.0)
    return seconds if seconds >= 0 else None
