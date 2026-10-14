"""What the player sees when something goes wrong — services down, a bug, a busy port, the game
quit, crew in another ship, the journal unreadable. Never live: scripted replies only."""

import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.gear import locate, parse_post
from edmultitool.core.settings import ScoutPrefs
from edmultitool.gear_lookup import lookup
from edmultitool.plugin import INTERNAL_MESSAGE, QUEUED, ScoutPlugin
from edmultitool.sources.http import (
    BadReply,
    HttpError,
    NetworkError,
    Request,
    Response,
    SourceError,
    brief,
    explain,
)
from fakes import FakeTime, ScriptedTransport, make_client, network_down, reply

DATA = Path(__file__).parent / "data"
SOL_ID = 10477373803
SERVICES = {"pad": "L", "services": ["Interstellar Factors Contact"]}
MARKET = {
    "commodity": "Gold", "side": "buy", "min_qty": 10, "pad": "M", "carriers": False, "max_age_days": 3,
    "hide_barred": True,
}  # fmt: skip


def _sample() -> list[dict[str, Any]]:
    lines = (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _played(tmp_path: Path, **kw: Any) -> ScoutPlugin:
    p = ScoutPlugin(tmp_path, **kw)
    for entry in _sample():
        p.on_journal(entry, None)
    p.on_journal(
        {"event": "FSDJump", "StarSystem": "Sol", "SystemAddress": SOL_ID, "StarPos": [0, 0, 0]}, None
    )
    return p


def _pump_until(p: ScoutPlugin, done: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert done()


@pytest.fixture
def down(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    """A plugin in Sol whose every online question fails (no connection)."""
    p = _played(tmp_path)
    t = ScriptedTransport(network_down(), repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()


# --- failures in player words -------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("exc", "said"),
    [
        (NetworkError("GET https://x: Read timed out.", timed_out=True), "no answer in time"),
        (NetworkError("POST https://x: HTTPSConnectionPool(...) Max retries"), "could not connect"),
        (HttpError(429, "GET https://x: HTTP 429"), "too many requests just now"),
        (HttpError(503, "GET https://x: HTTP 503"), "the site is down or overloaded (HTTP 503)"),
        (HttpError(503, "…; the server asks to wait 600 s", wait=600), "too busy, it asks to wait 10 min"),
        (HttpError(404, "GET https://x: HTTP 404"), "refused the request (HTTP 404)"),
        (HttpError(400, "riches submit: Could not find X", said="Could not find X"), "Could not find X"),
        (BadReply("reply is not JSON (HTTP 200)"), "sent a reply ED Multitool cannot read"),
        (SourceError("Spansh does not know 'Xx'"), "Spansh does not know 'Xx'"),
    ],
)  # fmt: skip
def test_failures_are_told_without_urls_or_library_text(exc: SourceError, said: str) -> None:
    text = explain(exc)
    assert brief(text).startswith(said)
    assert "http" not in text.lower().replace("(http ", "") and "Pool" not in text


def test_every_card_says_spansh_or_ardent_failed_in_one_plain_sentence(
    down: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, _t = down
    p.api_post("/api/services", SERVICES)
    p.api_post("/api/market", MARKET)
    p.api_post("/api/powerplay", {"power": "Li Yong-Rui", "mode": "held"})
    p.api_post("/api/gear", {"text": "Keian Gu - Hahn Terminal\nG3 Maverick - Night Vision"})

    def cards() -> tuple[Any, ...]:
        return (p.services, p.market, p.powerplay, p.gear)

    _pump_until(p, lambda: all(c.status == "error" for c in cards()), timeout=30)
    connect = "could not connect (no internet, or the site is down); try again in a minute."
    assert p.services.message == f"Spansh: {connect}"
    assert p.powerplay.message == f"Spansh: {connect}"
    assert p.gear.message == f"Spansh: {connect}"
    assert p.market.message == (
        "Spansh: could not connect (no internet, or the site is down), and the backup Ardent too; "
        "try again in a minute."
    )
    for card in cards():
        assert "https://" not in card.message


def test_name_suggestions_fail_plainly(down: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = down
    got = p.api_get("/api/systems", {"q": "Sol"})
    assert got is not None
    status, doc = got
    assert status == 502 and isinstance(doc, dict)
    assert str(doc["error"]).startswith("Spansh: could not connect")


# --- a bug never leaves a card "running" -------------------------------------------------------------
def test_a_bug_in_finishing_a_job_marks_only_its_card(
    down: tuple[ScoutPlugin, ScriptedTransport],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    p, _t = down

    def broken(_r: object) -> None:
        raise KeyError("oops")

    monkeypatch.setattr(p, "_finish_services", broken)
    p.api_post("/api/services", SERVICES)
    p.api_post("/api/powerplay", {"power": "Li Yong-Rui", "mode": "held"})
    _pump_until(p, lambda: p.services.status == "error" and p.powerplay.status == "error", timeout=30)
    assert p.services.message == INTERNAL_MESSAGE
    assert p.powerplay.message.startswith("Spansh: could not connect")  # the next result still came through
    assert p._services_job is None
    assert any(r.exc_info for r in caplog.records)  # the traceback is in the log


def test_a_bug_in_starting_a_search_marks_its_card(
    down: tuple[ScoutPlugin, ScriptedTransport], monkeypatch: pytest.MonkeyPatch
) -> None:
    p, _t = down

    def broken(_req: object) -> None:
        raise ValueError("oops")

    monkeypatch.setattr(p, "_start_market", broken)
    p.api_post("/api/market", MARKET)
    _pump_until(p, lambda: p.market.status == "error")
    assert (p.market.message, p.market.handled) == (INTERNAL_MESSAGE, 1)


# --- one worker: a lookup waits for the route search and says so ----------------------------------------
def test_a_lookup_during_a_route_search_says_it_waits(tmp_path: Path) -> None:
    p = _played(tmp_path)
    gate = threading.Event()

    def held(_request: Request) -> Response:
        gate.wait(5)
        return Response(500, b"{}")

    p.client = make_client(ScriptedTransport(held, repeat_last=True), FakeTime(), max_retries=0)
    p.start_worker()
    try:
        p._scout_job = (10**6, ScoutPrefs(), "Sol", 30.0)  # a route search under way
        p.api_post("/api/services", SERVICES)
        _pump_until(p, lambda: p.services.status == "running")
        assert (
            p.services.message
            == QUEUED + "looking for the nearest stations with a large pad, Interstellar Factors near Sol…"
        )
        p._commands.put(("cancel", None))  # the route search ends
        _pump_until(p, lambda: not p.services.message.startswith(QUEUED))
        assert (
            p.services.message
            == "Looking for the nearest stations with a large pad, Interstellar Factors near Sol…"
        )
        gate.set()
        _pump_until(p, lambda: p.services.status == "error")
    finally:
        gate.set()
        p.stop_worker()


# --- the gear finder: one name failing does not sink the post -------------------------------------------
def test_a_gear_name_spansh_fails_on_is_marked_and_the_rest_found() -> None:
    def spansh(request: Request) -> Response:
        if "/field_values/system_names" in request.url:
            if dict(request.params).get("q") == "Keian Gu":
                return reply("gear_names_keian")
            raise network_down()
        if request.url.endswith("/systems/search"):
            return reply("gear_systems_keian")
        raise network_down()

    post = parse_post("Keian Gu - Hahn Terminal\nG3 Maverick - Night Vision\n\nWolf 397\nG5 Dirty Drives")
    t = ScriptedTransport(spansh, repeat_last=True)
    found = lookup(make_client(t, FakeTime(), max_retries=0), post, threading.Event())
    assert [s.name for s in found.systems.values()] == ["Keian Gu"]
    assert "wolf397" in found.failed  # by name_key
    places = {p.text: p for p in locate(post, found)}
    assert places["Keian Gu - Hahn Terminal"].system is not None
    assert places["Wolf 397"].note == "Spansh did not answer for this name; try again"


def test_a_gear_post_fails_whole_only_when_nothing_came_through() -> None:
    post = parse_post("Keian Gu - Hahn Terminal\nG3 Maverick - Night Vision")
    t = ScriptedTransport(network_down(), repeat_last=True)
    with pytest.raises(NetworkError):
        lookup(make_client(t, FakeTime(), max_retries=0), post, threading.Event())


def test_a_failed_name_beside_an_unknown_one_keeps_the_post() -> None:
    def spansh(request: Request) -> Response:
        if dict(request.params).get("q") == "Wolf 397":
            return Response(200, b'{"min_max": []}')  # answered: no such system
        if request.url.endswith("/stations/search") and "Wolf 397" in (request.json_body or ""):
            return Response(200, b'{"results": []}')  # nor such station
        raise network_down()

    post = parse_post("Keian Gu\nG3 Maverick - Night Vision\n\nWolf 397\nG5 Dirty Drives")
    t = ScriptedTransport(spansh, repeat_last=True)
    found = lookup(make_client(t, FakeTime(), max_retries=0), post, threading.Event())
    places = {p.text: p.note for p in locate(post, found)}
    assert places == {"Keian Gu": "Spansh did not answer for this name; try again", "Wolf 397": "not found"}


# --- the game quit, crew in another ship ----------------------------------------------------------------
def test_after_the_game_quits_the_panel_says_last_seen(tmp_path: Path) -> None:
    p = _played(tmp_path)
    assert p.text.scout == "Sol" and not p.text.ship.endswith("last flown")
    p.on_journal({"event": "Shutdown"}, None)
    assert p.text.scout == "game closed · last in Sol"
    assert p.text.ship.endswith(" · last flown")
    assert p.ui.system == "Sol"  # searches still start from the last place
    p.on_journal(
        {"event": "Location", "StarSystem": "Sol", "SystemAddress": SOL_ID, "StarPos": [0, 0, 0]}, None
    )
    assert p.text.scout == "Sol" and not p.text.ship.endswith("last flown")


def test_a_ship_remembered_from_an_earlier_session_says_so(tmp_path: Path) -> None:
    first = _played(tmp_path)
    loadout = next(e for e in _sample() if e["event"] == "Loadout")
    first.on_journal({**loadout, "ShipID": 7}, None)  # a ship with an id is remembered (saved)
    again = ScoutPlugin(tmp_path)  # EDMC restarted, the game not running
    assert again.text.scout == "waiting for the game…"
    assert again.text.ship.endswith(" · last flown")


def test_the_hosts_ship_is_never_remembered_as_ours(tmp_path: Path) -> None:
    p = _played(tmp_path)
    ours = p.state.ship
    loadout = next(e for e in _sample() if e["event"] == "Loadout")
    p.on_journal({"event": "JoinACrew", "Captain": "Someone"}, None)
    p.on_journal({**loadout, "ShipID": 4242, "ShipIdent": "HOST-1"}, None)
    assert 4242 not in p.fleet.ships
    p.on_journal({"event": "QuitACrew", "Captain": "Someone"}, None)
    assert p.state.ship == ours


# --- the journal history, the page's port, the browser --------------------------------------------------
def test_an_unreadable_journal_folder_is_shown_on_the_page(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path, journal_dir=tmp_path / "no such folder")
    p.client = make_client(ScriptedTransport(), FakeTime())
    p.start_worker()
    try:
        (notice,) = p.ui.notices
        assert "journal folder" in notice and "cannot be read" in notice
        assert json.loads(p.snapshot)["state"]["notices"] == [notice]
    finally:
        p.stop_worker()


def test_a_failed_history_pass_is_shown_on_the_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr("edmultitool.plugin.scan_history", broken)
    journal = tmp_path / "journal"
    journal.mkdir()
    p = ScoutPlugin(tmp_path, journal_dir=journal)
    p.client = make_client(ScriptedTransport(), FakeTime())
    p.start_worker()
    try:
        assert not p.ui.notices
        _pump_until(p, lambda: bool(p.ui.notices))
        assert p.ui.notices[0].startswith("Reading the game's journal history failed")
    finally:
        p.stop_worker()


def test_a_busy_port_is_told_on_the_panel(tmp_path: Path) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        busy = taken.getsockname()[1]
        p = ScoutPlugin(tmp_path)
        url = p.start_server(port=busy)
        try:
            assert url is not None and f":{busy}/" not in url
            assert p.text.note == f"Port {busy} is busy (a second EDMC?): this page is at {url}"
        finally:
            p.stop_server()


def test_a_free_port_needs_no_note_and_a_failed_browser_does(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    p = ScoutPlugin(tmp_path)
    url = p.start_server(port=free)
    try:
        assert p.text.note == ""
        p.page_opened("app-window")
        assert p.text.note == ""
        p.page_opened("failed")
        assert p.text.note == f"No browser could be opened: open {url} yourself."
        p.page_opened("browser")  # it worked the next time
        assert p.text.note == ""
    finally:
        p.stop_server()
