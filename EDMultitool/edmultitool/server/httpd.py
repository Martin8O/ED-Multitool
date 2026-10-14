"""127.0.0.1-only HTTP server: serves `web/`, `GET /api/state` and the plugin's `Api`. No tkinter, no EDMC
imports.

Runs `serve_forever` on its own daemon thread; request threads call the `snapshot` callable, which must
return ready-made JSON bytes (the plugin swaps one immutable bytes object, so no lock is needed), and the
`Api` methods, which must be safe to call from any thread.

Defences (a page on any website can make the browser talk to 127.0.0.1):
- bind 127.0.0.1 only, exclusively (on Windows SO_REUSEADDR would let a second process share the port);
- Host must be 127.0.0.1:<port> or localhost:<port> — this defeats DNS rebinding;
- Origin, when sent, must be this server's own origin; cross-site fetch metadata is refused;
- POST (the only write) additionally needs an Origin equal to the origin the Host header names,
  `Content-Type: application/json` (no plain HTML form can send it, and a cross-site script would need a
  CORS preflight we never answer) and a Content-Length of at most MAX_BODY bytes, read exactly;
- everything other than GET/HEAD/POST is 405; never any CORS header;
- every error is JSON, so an unknown /api/ path can never look like success.
"""

import json
import logging
import socket
import socketserver
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, Protocol
from urllib.parse import parse_qsl

DEFAULT_PORT = 47615
MAX_BODY = 16 * 1024
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
}
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
_OK_FETCH_SITES = {"same-origin", "none"}

ApiReply = tuple[int, object]


class Api(Protocol):
    """What the plugin answers under /api/ besides the state snapshot. None = unknown path (404)."""

    def api_get(self, path: str, params: dict[str, str]) -> ApiReply | None: ...

    def api_post(self, path: str, doc: object) -> ApiReply | None: ...


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False
    allow_reuse_port = False
    # Must stay True: server_close() would otherwise join request threads, and an idle browser
    # connection could then hold up plugin_stop (and EDMC's exit) for up to _Handler.timeout.
    daemon_threads = True

    def __init__(
        self,
        port: int,
        snapshot: Callable[[], bytes],
        web_dir: Path,
        log: logging.Logger,
        api: Api | None,
    ) -> None:
        self.snapshot = snapshot
        self.api = api
        self.web_dir = web_dir.resolve()
        self.log = log
        super().__init__(("127.0.0.1", port), _Handler)

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)  # Windows only
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        # Skip HTTPServer.server_bind: its socket.getfqdn() reverse lookup can stall for seconds.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]

    def handle_error(self, request: object, client_address: object) -> None:
        """A client vanishing mid-request (browser closed, reset) is normal: log it, never print it
        to stderr like socketserver does by default."""
        self.log.debug("web: request from %s failed", client_address, exc_info=True)

    @property
    def port(self) -> int:
        return int(self.server_address[1])


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "EDMultitool"
    sys_version = ""
    error_message_format = ""  # send_error is overridden: errors are JSON
    timeout = 10  # an idle or half-sent connection frees its thread after this many seconds
    # Must stay HTTP/1.0 (one request per connection): refused POSTs are answered without reading their
    # body, and with keep-alive that unread body would be parsed as a second request — one that never
    # passed the Origin / Content-Type checks of the first.
    protocol_version = "HTTP/1.0"
    _METHODS: ClassVar[str] = "GET, HEAD, POST"

    # --- methods -------------------------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._origin_ok():
            self._json(403, {"error": "forbidden"})
            return
        path, _, query = self.path.split("#", 1)[0].partition("?")
        if path == "/api/state":
            self._send(200, "application/json; charset=utf-8", self.server.snapshot())
        elif path.startswith("/api/"):
            api = self.server.api
            params = dict(parse_qsl(query, keep_blank_values=True))
            self._api_reply(path, api.api_get(path, params) if api is not None else None)
        else:
            self._static(path)

    def do_HEAD(self) -> None:
        path = self.path.split("#", 1)[0].split("?", 1)[0]
        if path.startswith("/api/") and path != "/api/state":
            self._not_allowed()  # a HEAD must never spend a call to Spansh
            return
        self.do_GET()

    def do_POST(self) -> None:
        if not self._origin_ok() or not self._own_origin():
            self._json(403, {"error": "forbidden"})
            return
        path = self.path.split("#", 1)[0].split("?", 1)[0]
        if not path.startswith("/api/"):
            self._not_allowed()
            return
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._json(415, {"error": "send JSON (Content-Type: application/json)"})
            return
        length_text = self.headers.get("Content-Length", "").strip()
        if not (length_text.isascii() and length_text.isdigit()):  # "²" is a digit to isdigit()
            self._json(411, {"error": "Content-Length required"})
            return
        length = int(length_text)
        if length > MAX_BODY:
            self._json(413, {"error": f"body over {MAX_BODY} bytes"})
            return
        raw = self.rfile.read(length)  # a stalled sender is cut off by the handler timeout
        if len(raw) != length:  # the client gave up mid-body: nobody is left to answer
            self.close_connection = True
            return
        try:
            doc = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:  # UnicodeDecodeError included
            self._json(400, {"error": "body is not valid JSON"})
            return
        api = self.server.api
        self._api_reply(path, api.api_post(path, doc) if api is not None else None)

    def _not_allowed(self) -> None:
        self._json(405, {"error": "method not allowed"}, extra={"Allow": self._METHODS})

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _not_allowed

    # --- checks --------------------------------------------------------------------------------
    def _origin_ok(self) -> bool:
        port = self.server.port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "").strip().lower() not in hosts:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in {f"http://{h}" for h in hosts}:
            return False
        site = self.headers.get("Sec-Fetch-Site")
        return site is None or site.strip().lower() in _OK_FETCH_SITES

    def _own_origin(self) -> bool:
        """Writes need an Origin, and it must be exactly the origin the (already checked) Host names."""
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "").strip().lower()
        return origin is not None and origin.strip().lower() == f"http://{host}"

    # --- responses -----------------------------------------------------------------------------
    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        # Refuse before touching the filesystem: a backslash or drive could name a UNC/network path.
        parts = rel.split("/")
        if "\\" in rel or ":" in rel or ".." in parts or "" in parts:
            self._json(404, {"error": "not found", "path": path})
            return
        root = self.server.web_dir
        target = (root / rel).resolve()
        ctype = _CONTENT_TYPES.get(target.suffix.lower())
        if ctype is None or not target.is_relative_to(root) or not target.is_file():
            self._json(404, {"error": "not found", "path": path})
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._json(404, {"error": "not found", "path": path})
            return
        self._send(200, ctype, body)

    def _api_reply(self, path: str, reply: ApiReply | None) -> None:
        if reply is None:
            self._json(404, {"error": "not found", "path": path})
        else:
            self._json(*reply)

    def _json(self, code: int, doc: object, extra: dict[str, str] | None = None) -> None:
        body = (json.dumps(doc, ensure_ascii=False) + "\n").encode("utf-8")
        self._send(code, "application/json; charset=utf-8", body, extra)

    def _send(self, code: int, ctype: str, body: bytes, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for name, value in {**_SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Malformed requests (raised by BaseHTTPRequestHandler itself) also get a JSON error."""
        self.close_connection = True
        self._json(code, {"error": message or self.responses.get(code, ("error", ""))[0]})

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Only refused/failed requests reach EDMC's log; the page polls every second."""
        if not (isinstance(code, int) and code < 400):
            super().log_request(code, size)

    def log_message(self, format: str, *args: object) -> None:
        self.server.log.debug("web: " + format, *args)


class ScoutServer:
    """Owns the listening socket and its thread. `start()` once, `stop()` once (both idempotent)."""

    def __init__(
        self,
        snapshot: Callable[[], bytes],
        log: logging.Logger,
        *,
        port: int = DEFAULT_PORT,
        web_dir: Path = WEB_DIR,
        api: Api | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._log = log
        self._port = port
        self._web_dir = web_dir
        self._api = api
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int | None:
        return self._httpd.port if self._httpd is not None else None

    @property
    def url(self) -> str | None:
        return f"http://127.0.0.1:{self._httpd.port}/" if self._httpd is not None else None

    def start(self) -> str | None:
        """Bind the preferred port; if it is taken, let the OS pick a free one. None = no server."""
        if self._httpd is not None:
            return self.url
        for port in dict.fromkeys((self._port, 0)):
            try:
                self._httpd = _Server(port, self._snapshot, self._web_dir, self._log, self._api)
                break
            except OSError as exc:
                self._log.warning("web server: cannot listen on 127.0.0.1:%s (%s)", port, exc)
        if self._httpd is None:
            return None
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="EDMultitool-web",
            daemon=True,
        )
        self._thread.start()
        self._log.info("web server listening on %s", self.url)
        return self.url

    def stop(self, timeout: float = 2.0) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd = self._thread = None
        if httpd is None:
            return
        if thread is not None and thread.is_alive():
            httpd.shutdown()  # waits for serve_forever to return; only safe while it is running
            thread.join(timeout)
        httpd.server_close()
        self._log.info("web server stopped")
