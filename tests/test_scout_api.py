"""The page's write path: the server's POST checks, and the plugin's scout flow on recorded Spansh replies."""

import json
import logging
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.settings import ScoutPrefs, Settings, load_settings, parse_scout_prefs
from edmultitool.plugin import ScoutPlugin
from edmultitool.server import MAX_BODY, ApiReply, ScoutServer
from edmultitool.sources.http import Request, Response
from fakes import FakeTime, ScriptedTransport, make_client, recorded, reply

DATA = Path(__file__).parent / "data"
LOG = logging.getLogger("test.scout_api")
PREFS = asdict(ScoutPrefs(from_system="Sol", to_system="Colonia"))


# --- server: what a write needs ---------------------------------------------------------------------
class StubApi:
    def __init__(self) -> None:
        self.posts: list[tuple[str, object]] = []
        self.gets: list[tuple[str, dict[str, str]]] = []

    def api_get(self, path: str, params: dict[str, str]) -> ApiReply | None:
        self.gets.append((path, params))
        return (200, {"echo": params}) if path == "/api/systems" else None

    def api_post(self, path: str, doc: object) -> ApiReply | None:
        self.posts.append((path, doc))
        return (202, {"ok": True}) if path == "/api/scout" else None


@pytest.fixture
def served() -> Iterator[tuple[ScoutServer, StubApi, int]]:
    api = StubApi()
    srv = ScoutServer(lambda: b"{}\n", LOG, port=0, api=api)
    url = srv.start()
    assert url is not None
    yield srv, api, int(url.rsplit(":", 1)[1].rstrip("/"))
    srv.stop()


def _raw(port: int, data: bytes, *, read: bool = True) -> tuple[int, dict[str, Any]]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(data)
        if not read:
            return 0, {}
        buf = b""
        while chunk := s.recv(65536):
            buf += chunk
    head, _, body = buf.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), json.loads(body)


def _post(
    port: int,
    body: bytes,
    *,
    path: str = "/api/scout",
    origin: str | None = "own",
    ctype: str | None = "application/json",
    length: int | None = None,
    host: str | None = None,
) -> tuple[int, dict[str, Any]]:
    host = host or f"127.0.0.1:{port}"
    lines = [f"POST {path} HTTP/1.1", f"Host: {host}", "Connection: close"]
    if origin is not None:
        lines.append(f"Origin: {'http://' + host if origin == 'own' else origin}")
    if ctype is not None:
        lines.append(f"Content-Type: {ctype}")
    if length is not None or body:
        lines.append(f"Content-Length: {len(body) if length is None else length}")
    return _raw(port, ("\r\n".join(lines) + "\r\n\r\n").encode() + body)


def test_post_from_own_page_reaches_the_api(served: tuple[ScoutServer, StubApi, int]) -> None:
    _, api, port = served
    status, doc = _post(port, b'{"a": 1}')
    assert (status, doc) == (202, {"ok": True})
    status, _ = _post(port, b'{"a": 2}', host=f"localhost:{port}", ctype="application/json; charset=utf-8")
    assert status == 202
    assert api.posts == [("/api/scout", {"a": 1}), ("/api/scout", {"a": 2})]


@pytest.mark.parametrize(
    ("kw", "status"),
    [
        ({"origin": None}, 403),  # a write must name its origin
        ({"origin": "http://evil.example"}, 403),
        ({"origin": "null"}, 403),
        ({"origin": "http://localhost:{port}"}, 403),  # another local origin than the Host names
        ({"host": "evil.example:{port}"}, 403),  # DNS rebinding
        ({"ctype": "text/plain"}, 415),  # what a plain HTML form or a no-preflight fetch can send
        ({"ctype": "application/x-www-form-urlencoded"}, 415),
        ({"ctype": None}, 415),
        ({"path": "/index.html"}, 405),
        ({"path": "/api/nope"}, 404),
    ],
)
def test_post_refusals(served: tuple[ScoutServer, StubApi, int], kw: dict[str, Any], status: int) -> None:
    _, api, port = served
    kw = {k: v.format(port=port) if isinstance(v, str) else v for k, v in kw.items()}
    got, doc = _post(port, b"{}", **kw)
    assert got == status and "error" in doc
    assert all(path == "/api/nope" for path, _ in api.posts)  # only the 404 case reached the API


def test_post_body_limits(served: tuple[ScoutServer, StubApi, int]) -> None:
    _, api, port = served
    assert _post(port, b"")[0] == 411  # no Content-Length at all
    assert _post(port, b"x" * (MAX_BODY + 1))[0] == 413
    assert _post(port, b"{not json")[0] == 400
    assert _post(port, b"\xff\xfe")[0] == 400
    # b"\xb2" is "²" in latin-1 (how header bytes are read): a digit to str.isdigit(), not to int()
    head = f"POST /api/scout HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\n"
    raw = (head + "Content-Type: application/json\r\nConnection: close\r\n").encode()
    assert _raw(port, raw + b"Content-Length: \xb2\r\n\r\n{}")[0] == 411
    assert api.posts == []


def test_head_never_reaches_the_api(served: tuple[ScoutServer, StubApi, int]) -> None:
    _, api, port = served
    req = f"HEAD /api/systems?q=Sol HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(req.encode())
        status_line = sock.recv(200).split(b"\r\n", 1)[0]
    assert status_line.split()[1] == b"405"
    assert api.gets == []


def test_half_sent_post_does_not_hold_up_stop(served: tuple[ScoutServer, StubApi, int]) -> None:
    srv, api, port = served
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        head = f"POST /api/scout HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\n"
        s.sendall((head + "Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{").encode())
        time.sleep(0.2)
        started = time.monotonic()
        srv.stop()
        assert time.monotonic() - started < 3
    assert api.posts == []


def test_get_passes_query_to_the_api(served: tuple[ScoutServer, StubApi, int]) -> None:
    _, api, port = served

    def get(path: str) -> tuple[int, dict[str, Any]]:
        return _raw(
            port, f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode()
        )

    assert get("/api/systems?q=Col%20285") == (200, {"echo": {"q": "Col 285"}})
    assert get("/api/other")[0] == 404
    assert api.gets == [("/api/systems", {"q": "Col 285"}), ("/api/other", {})]


# --- the form -----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"to_system": "  "}, "choose a destination"),
        ({"plotter": "fast"}, "'plotter' must be one of"),
        ({"radius_ly": 0}, "'radius_ly' must be a number from 1 to 1000"),
        ({"max_stops": 2.5}, "'max_stops' must be a whole number"),
        ({"min_mapping": -1}, "'min_mapping' must be a whole number"),
        ({"min_scan": True}, "'min_scan' must be a whole number"),
        ({"bio": 1}, "'bio' must be true or false"),
        ({"to_system": "x" * 101}, "'to_system' must be text"),
    ],
)
def test_bad_forms_are_named(change: dict[str, object], message: str) -> None:
    result = parse_scout_prefs({**PREFS, **change})
    assert isinstance(result, str) and result.startswith(message)


def test_good_form_is_trimmed_and_typed() -> None:
    doc = {**PREFS, "to_system": "  Colonia ", "max_stops": 12.0, "radius_ly": 30}
    assert parse_scout_prefs(doc) == ScoutPrefs(
        from_system="Sol", to_system="Colonia", max_stops=12, radius_ly=30.0
    )
    assert isinstance(parse_scout_prefs({k: v for k, v in PREFS.items() if k != "bio"}), str)
    assert isinstance(parse_scout_prefs([1]), str)


# --- plugin: page → command → worker → route ----------------------------------------------------------
def _sample() -> list[dict[str, Any]]:
    lines = (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _spansh(request: Request) -> Response:
    """Answers like Spansh did for the recorded Sol → Colonia jobs (riches first, then bio)."""
    if request.method == "POST":
        _spansh.kind = "bio" if request.url.endswith("/exobiology/route") else "riches"  # type: ignore[attr-defined]
        return reply("riches_submit")
    return reply(f"{_spansh.kind}_done")  # type: ignore[attr-defined]


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path)
    for entry in _sample():
        p.on_journal(entry, None)
    t = ScriptedTransport(_spansh, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()


def _pump_until(p: ScoutPlugin, status: str, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while p.scout.status != status and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert p.scout.status == status, (p.scout.status, p.scout.message)


def test_scout_runs_both_searches_and_publishes_the_merged_route(
    plugin: tuple[ScoutPlugin, ScriptedTransport], tmp_path: Path
) -> None:
    p, t = plugin
    assert p.api_post("/api/scout", PREFS) == (202, {"accepted": "scout"})
    assert p.scout.status == "idle"  # nothing happens off the main thread
    _pump_until(p, "done")

    posts = [r for r in t.requests if r.method == "POST"]
    assert [r.url.rsplit("/api/", 1)[1] for r in posts] == ["riches/route", "exobiology/route"]
    forms = [dict(r.form or ()) for r in posts]
    range_ly = f"{p.ui.ranges.routing:.2f}"
    assert all(f["from"] == "Sol" and f["to"] == "Colonia" and f["range"] == range_ly for f in forms)

    state = json.loads(p.snapshot)["state"]
    route = state["scout"]["route"]
    raw = {k: recorded(f"{k}_done")["body"]["result"][1:-1] for k in ("riches", "bio")}
    assert len(route["stops"]) == len(raw["riches"]) + len(raw["bio"])  # no overlap in the recordings
    assert [s["along_ly"] for s in route["stops"]] == sorted(s["along_ly"] for s in route["stops"])
    assert all(isinstance(s["id64"], str) for s in route["stops"])

    qa = next(s for s in route["stops"] if s["name"] == "Qa'wakana")
    spansh_qa = next(s for s in raw["bio"] if s["name"] == "Qa'wakana")
    want = {b["name"]: sum(sp["value"] for sp in b["landmarks"]) for b in spansh_qa["bodies"]}
    assert {b["name"]: b["bio_value"] for b in qa["bodies"]} == want

    blua = next(s for s in route["stops"] if s["name"].startswith("Blua Eaec"))
    spansh_blua = next(s for s in raw["riches"] if s["name"].startswith("Blua Eaec"))
    assert blua["mapping_value"] == sum(b["estimated_mapping_value"] for b in spansh_blua["bodies"])

    # the form is remembered, and the debug dump says how it went
    assert load_settings(tmp_path / "data" / "settings.json").scout == ScoutPrefs(
        from_system="Sol", to_system="Colonia"
    )
    # …and so is the destination, offered again in the To field
    assert load_settings(tmp_path / "data" / "settings.json").recent_to == ("Colonia",)
    assert state["recent_to"] == ["Colonia"]
    dump = json.loads((tmp_path / "data" / "debug" / "panel.json").read_text(encoding="utf-8"))
    assert dump["scout"] == {
        "status": "done",
        "message": "",
        "stops": len(route["stops"]),
        "mode": "tour",
        "waypoints": 0,
    }


def test_prime_planets_ask_for_the_big_list_and_keep_the_stop_count(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, t = plugin
    prefs = {**PREFS, "bio": False, "planets": "prime", "max_stops": 2, "max_detour_ly": 0}
    assert p.api_post("/api/scout", prefs) == (202, {"accepted": "scout"})
    _pump_until(p, "done")
    (post,) = [dict(r.form or ()) for r in t.requests if r.method == "POST"]
    assert post["max_results"] == "100"  # Spansh cannot filter by type: we ask for many, keep the best
    route = json.loads(p.snapshot)["state"]["scout"]["route"]
    assert len(route["stops"]) == 2  # of the 5 recorded systems
    assert all(
        b["subtype"] in ("Earth-like world", "Water world") for s in route["stops"] for b in s["bodies"]
    )


def test_a_failed_scout_does_not_remember_its_destination(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)  # no journal: no start system, the search never runs
    assert p.api_post("/api/scout", {**PREFS, "from_system": "", "to_system": "Colnia"}) == (
        202,
        {"accepted": "scout"},
    )
    p.pump()
    assert p.scout.status == "error"
    assert p.settings.recent_to == ()


def test_scout_from_the_current_system_when_from_is_empty(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, t = plugin
    p.api_post("/api/scout", {**PREFS, "from_system": "", "bio": False})
    _pump_until(p, "done")
    (post,) = [r for r in t.requests if r.method == "POST"]
    assert dict(post.form or ())["from"] == p.state.system


def test_spansh_error_is_shown(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    t.steps = [reply("error_unknown_system")]
    p.api_post("/api/scout", PREFS)
    _pump_until(p, "error")
    assert p.scout.message == "Spansh does not know the start system 'Sol'. Pick a name from the suggestions."
    assert p.scout.route is None


def test_cancel_stops_the_search_and_a_late_result_is_ignored(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, t = plugin
    gate = threading.Event()

    def slow(request: Request) -> Response:
        gate.wait(5)
        return _spansh(request)

    t.steps = [slow]
    p.api_post("/api/scout", PREFS)
    _pump_until(p, "running")
    assert p.api_post("/api/scout/cancel", {}) == (202, {"accepted": "cancel"})
    _pump_until(p, "idle")
    assert p.scout.message == "Search cancelled."
    gate.set()
    time.sleep(0.2)
    p.pump()
    assert p.scout.status == "idle" and p.scout.route is None


def test_without_a_range_or_start_the_page_is_told(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.api_post("/api/scout", {**PREFS, "from_system": ""})
    p.pump()
    assert p.scout.status == "error" and p.scout.message.startswith("Start system unknown")
    p.api_post("/api/scout", PREFS)
    p.pump()
    assert p.scout.status == "error" and p.scout.message.startswith("No jump range yet")


def test_bad_form_is_refused_and_nothing_is_queued(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    status, doc = p.api_post("/api/scout", {**PREFS, "to_system": ""}) or (0, {})
    assert status == 400 and doc == {"error": "choose a destination"}
    assert p.api_post("/api/unknown", {}) is None
    assert not p.pump() and p.scout.status == "idle" and p.seq == 0


def test_suggestions_answer_on_the_request_thread(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    t.steps = [reply("names")]
    got = p.api_get("/api/systems", {"q": "Col 285 Sector"})
    assert got is not None and got[0] == 200
    doc: dict[str, Any] = got[1]  # type: ignore[assignment]
    assert doc["q"] == "Col 285 Sector"
    raw = recorded("names")["body"]["min_max"]
    assert [s["name"] for s in doc["systems"]] == [r["name"] for r in raw]
    assert doc["systems"][0]["id64"] == str(raw[0]["id64"])
    assert p.api_get("/api/systems", {"q": "C"}) == (200, {"q": "C", "systems": []})  # too short: no call
    assert p.api_get("/api/elsewhere", {}) is None


def test_newer_suggestion_request_cancels_the_older_one(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, t = plugin
    entered, release = threading.Event(), threading.Event()

    def slow_names(request: Request) -> Response:
        entered.set()
        release.wait(5)
        return reply("names")

    t.steps = [slow_names, reply("names")]
    first: list[ApiReply | None] = []
    older = threading.Thread(target=lambda: first.append(p.api_get("/api/systems", {"q": "Col 28"})))
    older.start()
    assert entered.wait(5)  # the older request is now waiting on Spansh
    second = p.api_get("/api/systems", {"q": "Col 285"})
    release.set()
    older.join(5)
    assert second is not None and second[0] == 200
    assert first == [(200, {"q": "Col 28", "systems": [], "superseded": True})]


def test_each_handled_command_is_counted(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.api_post("/api/scout", {**PREFS, "from_system": ""})
    p.pump()
    first = p.scout
    p.api_post("/api/scout", {**PREFS, "from_system": ""})
    assert p.pump() is False
    # the same error again is still a new answer the page can tell apart
    assert (p.scout.status, p.scout.message) == (first.status, first.message)
    assert (first.handled, p.scout.handled) == (1, 2)
    p.api_post("/api/scout/cancel", {})
    p.pump()
    assert p.scout.handled == 3 and p.scout.status == "error"  # nothing was running: state kept
    assert json.loads(p.snapshot)["state"]["scout"]["handled"] == 3


def test_broken_saved_scout_values_fall_back_one_by_one(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    saved = {**PREFS, "radius_ly": "wide", "max_stops": 0, "bio": "yes", "min_mapping": 2_000_000.0}
    path.write_text(json.dumps({"route_basis": "now", "margin_pct": 5, "scout": saved}), encoding="utf-8")
    got = load_settings(path)
    assert got.route_basis == "now" and got.margin_pct == 5.0
    default = ScoutPrefs()
    assert got.scout == ScoutPrefs(
        from_system="Sol", to_system="Colonia", radius_ly=default.radius_ly, max_stops=default.max_stops,
        bio=default.bio, min_mapping=2_000_000,
    )  # fmt: skip
    assert type(got.scout.min_mapping) is int
    path.write_text(json.dumps({"scout": "garbage"}), encoding="utf-8")
    assert load_settings(path).scout == ScoutPrefs()


def test_tk_settings_tab_keeps_the_scout_form(tmp_path: Path) -> None:
    from edmultitool.core.settings import settings_from_form

    current = Settings(scout=ScoutPrefs(to_system="Colonia"))
    assert settings_from_form("full tank", "10", current).scout == current.scout
