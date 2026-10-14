"""The localhost page server: what it serves, what it refuses, and that it really stops."""

import http.client
import json
import logging
import socket
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from edmultitool import browser
from edmultitool.browser import app_window_command, find_app_browser, open_page
from edmultitool.core.settings import Settings
from edmultitool.core.state import GameState
from edmultitool.core.view import panel_text, ui_state
from edmultitool.plugin import ScoutPlugin
from edmultitool.server import WEB_DIR, ScoutServer

DATA = Path(__file__).parent / "data"
LOG = logging.getLogger("test.server")
SNAPSHOT = b'{"seq": 7}\n'


@pytest.fixture
def server() -> Iterator[ScoutServer]:
    srv = ScoutServer(lambda: SNAPSHOT, LOG, port=0)
    assert srv.start() is not None
    yield srv
    srv.stop()


def _port(srv: ScoutServer) -> int:
    assert srv.url is not None
    return int(srv.url.rsplit(":", 1)[1].rstrip("/"))


def _request(
    srv: ScoutServer, path: str, *, method: str = "GET", headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    port = _port(srv)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in hdrs.items():
            conn.putheader(name, value)
        conn.endheaders()
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def test_state_endpoint_returns_snapshot_with_safe_headers(server: ScoutServer) -> None:
    status, headers, body = _request(server, "/api/state")
    assert status == 200
    assert body == SNAPSHOT
    assert headers["content-type"].startswith("application/json")
    assert headers["cache-control"] == "no-store"
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in headers


def test_localhost_name_and_own_origin_are_accepted(server: ScoutServer) -> None:
    port = _port(server)
    for host in (f"localhost:{port}", f"LOCALHOST:{port}"):
        status, _, _ = _request(server, "/api/state", headers={"Host": host, "Origin": f"http://{host}"})
        assert status == 200
    status, _, _ = _request(server, "/api/state", headers={"Sec-Fetch-Site": "same-origin"})
    assert status == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example"},  # DNS rebinding: evil.example resolved to 127.0.0.1
        {"Host": "evil.example:47615"},
        {"Host": "127.0.0.1"},  # wrong port
        {"Host": ""},
        {"Origin": "http://evil.example"},
        {"Origin": "http://127.0.0.1:1"},  # another local server
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_foreign_host_origin_or_site_is_refused(server: ScoutServer, headers: dict[str, str]) -> None:
    status, _, body = _request(server, "/api/state", headers=headers)
    assert status == 403
    assert json.loads(body) == {"error": "forbidden"}
    assert SNAPSHOT not in body
    status, _, _ = _request(server, "/", headers=headers)
    assert status == 403


def test_page_and_assets_are_served(server: ScoutServer) -> None:
    status, headers, body = _request(server, "/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert body == (WEB_DIR / "index.html").read_bytes()
    assert "default-src 'self'" in headers["content-security-policy"]
    for name, ctype in (
        ("app.js", "text/javascript"),
        ("theme.js", "text/javascript"),
        ("style.css", "text/css"),
    ):
        status, headers, body = _request(server, f"/{name}?v=1")
        assert status == 200
        assert headers["content-type"].startswith(ctype)
        assert body == (WEB_DIR / name).read_bytes()


@pytest.mark.parametrize(
    "path",
    [
        "/api/nope",
        "/api/",
        "/missing.html",
        "/../pyproject.toml",
        "/../../EDMultitool/load.py",
        "/..%2f..%2fload.py",
        "/..\\..\\load.py",
        "/\\\\evil\\share\\x.html",
        "/C:/Windows/win.ini",
        "//evil/share/x.html",
        "/web/../index.html",
        "/load.py",
    ],
)
def test_unknown_or_escaping_paths_get_json_404(server: ScoutServer, path: str) -> None:
    status, headers, body = _request(server, path)
    assert status == 404
    assert headers["content-type"].startswith("application/json")
    assert json.loads(body)["error"] == "not found"


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_other_writes_are_not_allowed(server: ScoutServer, method: str) -> None:
    status, headers, body = _request(server, "/api/state", method=method)
    assert status == 405
    assert headers["allow"] == "GET, HEAD, POST"
    assert json.loads(body) == {"error": "method not allowed"}


def test_post_without_origin_is_refused(server: ScoutServer) -> None:
    status, _, body = _request(server, "/api/state", method="POST")
    assert status == 403  # the full POST rules are in test_scout_api.py
    assert json.loads(body) == {"error": "forbidden"}


def test_head_has_headers_but_no_body(server: ScoutServer) -> None:
    status, headers, body = _request(server, "/api/state", method="HEAD")
    assert status == 200
    assert headers["content-length"] == str(len(SNAPSHOT))
    assert body == b""


def test_garbage_request_gets_json_error(server: ScoutServer) -> None:
    with socket.create_connection(("127.0.0.1", _port(server)), timeout=5) as sock:
        sock.sendall(b"BREW /pot HTTP/1.1\r\nHost: x\r\n\r\n")
        reply = b""
        while chunk := sock.recv(4096):  # the server closes the connection after an error
            reply += chunk
    head, _, body = reply.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 501")
    assert b"application/json" in head
    assert json.loads(body)["error"]


def test_busy_port_falls_back_to_a_free_one() -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        busy = blocker.getsockname()[1]
        srv = ScoutServer(lambda: SNAPSHOT, LOG, port=busy)
        try:
            url = srv.start()
            assert url is not None
            assert _port(srv) != busy
            assert _request(srv, "/api/state")[0] == 200
        finally:
            srv.stop()


def test_stop_releases_the_port_and_is_idempotent() -> None:
    srv = ScoutServer(lambda: SNAPSHOT, LOG, port=0)
    srv.start()
    port = _port(srv)
    assert srv.start() == f"http://127.0.0.1:{port}/"  # second start is a no-op
    srv.stop()
    srv.stop()
    assert srv.url is None
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    again = ScoutServer(lambda: SNAPSHOT, LOG, port=port)  # the exact port is free again
    try:
        assert again.start() == f"http://127.0.0.1:{port}/"
    finally:
        again.stop()


# --- the state the page renders ---------------------------------------------------------------


def _sample() -> list[dict[str, Any]]:
    lines = (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_ui_state_carries_panel_text_and_rounded_ranges(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    for entry in _sample():
        plugin.on_journal(entry, None)
    st, settings = plugin.state, Settings(route_basis="full", margin_pct=20)
    ui = ui_state(st, settings, game_seen=True)
    assert ui.panel == panel_text(st, settings, game_seen=True)
    full = st.full_tank_range()
    assert full is not None and ui.ranges.full == round(full, 2)
    assert ui.ranges.routing == round(full * 0.8, 2)
    # Same digits as the panel string, so the page can never disagree with it.
    assert f"{ui.ranges.routing:.2f} ly" in ui.panel.routing
    assert ui_state(GameState(), Settings(), game_seen=False).ranges.now is None


def test_snapshot_json_follows_every_change(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    first = json.loads(plugin.snapshot)
    assert first["seq"] == 0
    assert first["state"]["panel"]["scout"] == "waiting for the game…"
    jumps = [e for e in _sample() if e["event"] == "FSDJump"]
    for entry in _sample():
        plugin.on_journal(entry, None)
    doc = json.loads(plugin.snapshot)
    assert doc["seq"] > 0
    assert doc["state"]["system"] == jumps[-1]["StarSystem"]
    assert doc["state"]["address"] == str(jumps[-1]["SystemAddress"])
    seq = doc["seq"]
    plugin.on_journal(jumps[-1], None)  # nothing new: no new snapshot
    assert json.loads(plugin.snapshot)["seq"] == seq


def test_plugin_server_serves_its_live_snapshot(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    url = plugin.start_server(port=0)
    try:
        assert url is not None and plugin.server is not None
        jump = next(e for e in _sample() if e["event"] == "FSDJump")
        plugin.on_journal(jump, None)
        _, _, body = _request(plugin.server, "/api/state")
        assert json.loads(body)["state"]["system"] == jump["StarSystem"]
    finally:
        plugin.stop_server()
    assert plugin.server_url is None


# --- opening the page ---------------------------------------------------------------------------


def _fake_browser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)
    return opened


def test_app_window_command() -> None:
    cmd = app_window_command(r"C:\Edge\msedge.exe", "http://127.0.0.1:47615/")
    assert cmd[0] == r"C:\Edge\msedge.exe"
    assert "--app=http://127.0.0.1:47615/" in cmd


def test_open_page_without_app_browser_uses_default_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = _fake_browser(monkeypatch)
    monkeypatch.setattr("edmultitool.browser.find_app_browser", lambda: None)
    assert open_page("http://127.0.0.1:1/") == "browser"
    assert opened == ["http://127.0.0.1:1/"]


def test_open_page_falls_back_when_app_browser_cannot_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened = _fake_browser(monkeypatch)
    assert open_page("http://127.0.0.1:1/", browser=str(tmp_path / "no-such-browser.exe")) == "browser"
    assert opened == ["http://127.0.0.1:1/"]


@pytest.mark.skipif(sys.platform != "win32", reason="app-window lookup is Windows-only")
def test_app_browser_prefers_chromium_default_then_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    brave = tmp_path / "Brave" / "brave.exe"
    chrome = tmp_path / "Chrome" / "chrome.exe"
    firefox = tmp_path / "Firefox" / "firefox.exe"
    for exe in (brave, chrome, firefox):
        exe.parent.mkdir()
        exe.write_bytes(b"")
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.setenv(env, str(tmp_path / "nothing-here"))
    registered = {"chrome.exe": str(chrome)}
    monkeypatch.setattr("edmultitool.browser._registered_exe", registered.get)

    monkeypatch.setattr("edmultitool.browser._default_browser_exe", lambda: str(brave))
    assert find_app_browser() == str(brave)  # Chromium default browser wins
    monkeypatch.setattr("edmultitool.browser._default_browser_exe", lambda: str(firefox))
    assert find_app_browser() == str(chrome)  # Firefox has no app windows: next installed Chromium
    registered.clear()
    assert find_app_browser() is None  # → open_page falls back to a normal tab


def test_stop_does_not_wait_for_idle_or_half_sent_connections() -> None:
    srv = ScoutServer(lambda: SNAPSHOT, LOG, port=0)
    srv.start()
    port = _port(srv)
    idle = socket.create_connection(("127.0.0.1", port), timeout=5)  # like a browser preconnect
    half = socket.create_connection(("127.0.0.1", port), timeout=5)
    half.sendall(b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1")  # request never finished
    time.sleep(0.3)  # let the server accept both, so request threads really are waiting
    try:
        t0 = time.monotonic()
        srv.stop()
        assert time.monotonic() - t0 < 1.5
    finally:
        idle.close()
        half.close()


@pytest.mark.parametrize(
    "path",
    ["/../secret.js", "/..\\secret.js", "/sub/../../secret.js", "/%2e%2e/secret.js", "/D:/secret.js"],
)
def test_files_beside_the_web_folder_are_unreachable(tmp_path: Path, path: str) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / "secret.js").write_text("SECRET", encoding="utf-8")  # an allowed file type, outside web/
    srv = ScoutServer(lambda: SNAPSHOT, LOG, port=0, web_dir=web)
    srv.start()
    try:
        assert _request(srv, "/")[2] == b"ok"
        status, _, body = _request(srv, path)
        assert status == 404
        assert b"SECRET" not in body
    finally:
        srv.stop()


def test_default_browser_read_from_newest_registry_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assoc = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http"
    registry = {
        ("HKCU", rf"{assoc}\UserChoiceLatest\ProgId", "ProgId"): "BraveHTML",
        ("HKCU", rf"{assoc}\UserChoice", "ProgId"): "ChromeHTML",  # stale legacy mirror
        ("HKCR", r"BraveHTML\shell\open\command", ""): r'"C:\B\brave.exe" --single-argument %1',
        ("HKCR", r"ChromeHTML\shell\open\command", ""): r'"C:\C\chrome.exe" -- "%1"',
    }
    monkeypatch.setattr(
        "edmultitool.browser._reg_str", lambda hive, path, value="": registry.get((hive, path, value))
    )
    assert browser._default_browser_exe() == r"C:\B\brave.exe"
    del registry[("HKCU", rf"{assoc}\UserChoiceLatest\ProgId", "ProgId")]  # older Windows build
    assert browser._default_browser_exe() == r"C:\C\chrome.exe"
