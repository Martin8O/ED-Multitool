"""Nearest system by Powerplay power (Spansh, recorded replies)."""

import json
import math
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from edmultitool.catalog.powers import POWERS
from edmultitool.core.powerplay import (
    PowerQuery,
    PowerRequest,
    UiPowerplay,
    UiPowerRow,
    describe,
    effect,
    found_message,
    parse_power_request,
    ui_power_rows,
    with_distances,
)
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources import spansh
from edmultitool.sources.http import BadReply, Request, Response
from fakes import FakeTime, ScriptedTransport, make_client

DATA = Path(__file__).parent / "data"
SOL = (0.0, 0.0, 0.0)
SOL_ID = 10477373803
LYR = "Li Yong-Rui"


def _recorded(mode: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(
        (DATA / "spansh" / f"power_sol_lyr_{mode}.json").read_text(encoding="utf-8")
    )
    return doc


def _reply(mode: str) -> Response:
    doc = _recorded(mode)
    return Response(doc["status"], json.dumps(doc["body"]).encode("utf-8"))


def _query(mode: str = "held", state: str = "") -> PowerQuery:
    return PowerQuery(PowerRequest(LYR, mode, state), "Sol", SOL)  # type: ignore[arg-type]


# --- the request Spansh gets --------------------------------------------------------------------------
def test_form_matches_the_recorded_requests() -> None:
    for mode in ("held", "others", "acquire"):
        assert spansh.power_form(_query(mode)) == _recorded(mode)["request"]["json"]


def test_form_filters() -> None:
    held = spansh.power_form(_query("held"))["filters"]
    assert held == {
        "controlling_power": {"value": [LYR]},
        "power_state": {"value": ["Exploited", "Fortified", "Stronghold"]},
    }
    assert spansh.power_form(_query("held", "Fortified"))["filters"]["power_state"] == {
        "value": ["Fortified"]
    }
    others = spansh.power_form(_query("others"))["filters"]
    assert others["power"] == {"value": [LYR]}
    assert others["controlling_power"] == {"value": [p for p in POWERS if p != LYR]}
    assert others["power_state"] == {"value": ["Exploited", "Fortified", "Stronghold"]}
    assert spansh.power_form(_query("others", "Stronghold"))["filters"]["power_state"] == {
        "value": ["Stronghold"]
    }
    acquire = spansh.power_form(_query("acquire"))["filters"]
    assert acquire == {"power": {"value": [LYR]}, "power_state": {"value": ["Unoccupied"]}}
    q = spansh.power_form(PowerQuery(PowerRequest(LYR), "Here", (1.5, -2.0, 3.25)))
    assert q["reference_coords"] == {"x": 1.5, "y": -2.0, "z": 3.25}
    assert (q["size"], q["page"], q["sort"]) == (10, 0, [{"distance": {"direction": "asc"}}])


# --- the reply ---------------------------------------------------------------------------------------
def test_held_rows_from_the_recorded_reply() -> None:
    t = ScriptedTransport(_reply("held"))
    found = spansh.power_systems(make_client(t, FakeTime()), _query("held"), threading.Event())
    (req,) = t.requests
    assert req.method == "POST" and req.url == "https://spansh.co.uk/api/systems/search"
    assert found.count == 2185 and len(found.rows) == 10
    first = found.rows[0]
    assert (first.system, first.system_id64, first.held_by, first.state) == (
        "Sirius",
        121569805492,
        LYR,
        "Stronghold",
    )
    assert (first.progress, first.reinforcement, first.undermining) == (0.905325, 218800, 114875)
    assert first.updated == "2026-10-12T19:26:45Z" and LYR in first.powers
    assert all(r.held_by == LYR for r in found.rows)
    dists = [math.dist(SOL, (r.x, r.y, r.z)) for r in found.rows]
    assert dists == sorted(dists)


def test_others_rows_are_held_by_another_power() -> None:
    found = spansh.power_systems(
        make_client(ScriptedTransport(_reply("others")), FakeTime()), _query("others"), threading.Event()
    )
    assert found.count == 3206 and len(found.rows) == 10
    assert found.rows[0].system == "Sol" and found.rows[0].held_by == "Jerome Archer"
    assert all(LYR in r.powers and r.held_by and r.held_by != LYR for r in found.rows)


def test_acquire_rows_are_unoccupied() -> None:
    found = spansh.power_systems(
        make_client(ScriptedTransport(_reply("acquire")), FakeTime()), _query("acquire"), threading.Event()
    )
    assert found.count == 8013 and found.rows[0].system == "Stein 2051"
    assert all(LYR in r.powers and r.state == "Unoccupied" and not r.held_by for r in found.rows)


def test_unoccupied_row_has_no_progress() -> None:
    row = {"id64": 5, "name": "Stein 2051", "x": 1, "y": 2, "z": 3, "controlling_power": None,
           "power": [LYR, "Yuri Grom"], "power_state": "Unoccupied", "power_state_control_progress": None,
           "power_state_reinforcement": None, "power_state_undermining": None,
           "updated_at": "2026-10-12T07:00:58Z"}  # fmt: skip
    body = json.dumps({"count": 1, "results": [row]}).encode()
    found = spansh.power_systems(
        make_client(ScriptedTransport(Response(200, body)), FakeTime()), _query("acquire"), threading.Event()
    )
    (r,) = found.rows
    assert (r.held_by, r.state, r.progress, r.reinforcement, r.undermining) == (
        "",
        "Unoccupied",
        None,
        None,
        None,
    )
    (ui,) = ui_power_rows(LYR, found.rows)
    assert ui.progress_pct is None and ui.others == ("Yuri Grom",)


def test_unoccupied_row_as_spansh_sends_it() -> None:
    """Spansh leaves the controller, progress and merits out of an Unoccupied row (docs/api-notes.md)."""
    row = {"id64": 6, "name": "NLTT 48288", "x": 1, "y": 2, "z": 3, "power": [LYR, "Aisling Duval"],
           "power_state": "Unoccupied", "updated_at": "2026-10-12T13:00:00Z"}  # fmt: skip
    body = json.dumps({"count": 1, "results": [row]}).encode()
    found = spansh.power_systems(
        make_client(ScriptedTransport(Response(200, body)), FakeTime()), _query("acquire"), threading.Event()
    )
    (r,) = found.rows
    assert (r.held_by, r.progress, r.reinforcement, r.undermining) == ("", None, None, None)


def test_cached_search_is_not_asked_twice(tmp_path: Path) -> None:
    t = ScriptedTransport(_reply("held"))
    client = make_client(t, FakeTime(), cache_dir=tmp_path)
    a = spansh.power_systems(client, _query("held"), threading.Event())
    b = spansh.power_systems(client, _query("held"), threading.Event())
    assert a == b and len(t.requests) == 1


def test_bad_reply_is_refused() -> None:
    t = ScriptedTransport(Response(200, b'{"count": 1}'))
    with pytest.raises(BadReply):
        spansh.power_systems(make_client(t, FakeTime()), _query(), threading.Event())


# --- what the page shows ------------------------------------------------------------------------------
def test_ui_rows_and_distances() -> None:
    found = spansh.power_systems(
        make_client(ScriptedTransport(_reply("held")), FakeTime()), _query("held"), threading.Event()
    )
    rows = ui_power_rows(LYR, found.rows)
    assert rows[0].progress_pct == 91 and rows[0].system_id64 == "121569805492"
    assert LYR not in rows[0].others and len(rows[0].others) == 7
    pp = with_distances(UiPowerplay(rows=rows), SOL, 20.0)
    assert (pp.rows[0].distance_ly, pp.rows[0].jumps) == (8.59, 1)
    assert pp.rows[1].distance_ly == 14.01
    assert with_distances(UiPowerplay(rows=rows), None, 20.0).rows[0].distance_ly is None


def test_what_your_merits_do() -> None:
    def row(held: str, powers: tuple[str, ...]) -> UiPowerRow:
        return UiPowerRow(
            "S", "1", held, (), powers, "Unoccupied" if not held else "Exploited", None, None, None, ""
        )

    assert effect(row(LYR, (LYR, "Yuri Grom")), LYR) == "reinforce"
    assert effect(row("Yuri Grom", (LYR, "Yuri Grom")), LYR) == "undermine"
    assert effect(row("Yuri Grom", ("Yuri Grom",)), LYR) == "undermine"
    assert effect(row("", (LYR, "Yuri Grom")), LYR) == "acquire"
    assert effect(row("", ("Yuri Grom",)), LYR) == "out-of-reach"
    assert effect(row(LYR, (LYR,)), "") == ""
    pp = with_distances(UiPowerplay(rows=(row("Yuri Grom", ("Yuri Grom",)),)), SOL, 20.0, LYR)
    assert pp.rows[0].effect == "undermine"


def test_describe() -> None:
    assert describe(PowerRequest(LYR)) == "systems Li Yong-Rui holds"
    assert describe(PowerRequest(LYR, "held", "Fortified")) == "Fortified systems Li Yong-Rui holds"
    assert (
        describe(PowerRequest(LYR, "others", "Stronghold"))
        == "Stronghold systems another power holds, Li Yong-Rui present"
    )
    assert describe(PowerRequest(LYR, "acquire")) == "Unoccupied systems Li Yong-Rui can acquire"


def test_found_message() -> None:
    held = PowerRequest(LYR)
    assert found_message(held, 10, 2185, 8.6) == "Li Yong-Rui holds 2,185 systems: the 10 nearest."
    assert found_message(PowerRequest(LYR, "held", "Fortified"), 1, 1, 3.0) == (
        "Li Yong-Rui holds 1 Fortified system."
    )
    assert found_message(PowerRequest(LYR, "others"), 10, 3206, 0.0) == (
        "Li Yong-Rui is present in 3,206 systems another power holds: the 10 nearest."
    )
    assert found_message(PowerRequest(LYR, "acquire"), 10, 12000, 1754.4) == (
        "Li Yong-Rui can acquire more than 10,000 Unoccupied systems: the 10 nearest."
        " None is closer than 1,754 ly from where you searched."
    )
    assert found_message(held, 10, 2185, 199.9).endswith("nearest.")  # under 200 ly: no far note
    assert found_message(held, 0, 0, None) == "No systems Li Yong-Rui holds."
    assert found_message(PowerRequest(LYR, "held", "Stronghold"), 3, 3, 9.0) == (
        "Li Yong-Rui holds 3 Stronghold systems, all shown."
    )
    assert parse_power_request({"power": LYR, "mode": "acquire", "state": "Unoccupied"}) == (
        "state must be empty for acquire"
    )


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ([], "send a JSON object"),
        ({"power": "Zachary Hudson", "mode": "held"}, "power must be"),
        ({"power": LYR, "mode": "near"}, "mode must be"),
        ({"power": LYR, "mode": "held", "state": "Unoccupied"}, "state must be"),
        ({"power": LYR, "mode": "others", "state": "Contested"}, "state must be"),
        ({"power": LYR, "mode": "acquire", "state": "Unoccupied"}, "state must be"),
        ({"power": LYR, "mode": "present"}, "mode must be"),
        ({"power": LYR, "mode": []}, "mode must be"),
        ({"power": [], "mode": "held"}, "power must be"),
        ({"power": LYR, "mode": "held", "state": {}}, "state must be"),
    ],
)
def test_bad_requests(raw: object, error: str) -> None:
    got = parse_power_request(raw)
    assert isinstance(got, str) and got.startswith(error)


def test_good_requests() -> None:
    assert len(POWERS) == 12
    for p in POWERS:
        assert parse_power_request({"power": p, "mode": "held"}) == PowerRequest(p, "held", "")
    assert parse_power_request({"power": LYR, "mode": "others", "state": "Stronghold"}) == PowerRequest(
        LYR, "others", "Stronghold"
    )
    assert parse_power_request({"power": LYR, "mode": "acquire"}) == PowerRequest(LYR, "acquire", "")


# --- the plugin end to end --------------------------------------------------------------------------
def _route(request: Request) -> Response:
    if request.url.endswith("/systems/search") and request.json_body is not None:
        filters = json.loads(request.json_body)["filters"]
        if "power" not in filters:
            return _reply("held")
        return _reply("others" if "controlling_power" in filters else "acquire")
    return Response(500, b"{}")


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path)
    for line in (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            p.on_journal(json.loads(line), None)
    p.on_journal(
        {"event": "FSDJump", "StarSystem": "Sol", "SystemAddress": SOL_ID, "StarPos": [0, 0, 0]}, None
    )
    t = ScriptedTransport(_route, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()


def _pump_until(p: ScoutPlugin, done: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert done(), p.powerplay


def test_powerplay_search_end_to_end(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    assert p.api_post("/api/powerplay", {"power": LYR, "mode": "held", "state": ""}) == (
        202,
        {"accepted": "powerplay"},
    )
    _pump_until(p, lambda: p.powerplay.status == "done")
    v = p.ui.powerplay
    assert (v.handled, v.source, v.searched_from, v.power, v.mode, v.count) == (
        1,
        "Spansh",
        "Sol",
        LYR,
        "held",
        2185,
    )
    assert v.message == "Li Yong-Rui holds 2,185 systems: the 10 nearest."
    assert (v.rows[0].system, v.rows[0].distance_ly, v.rows[0].state) == ("Sirius", 8.59, "Stronghold")
    state = json.loads(p.snapshot)["state"]
    assert state["powerplay"]["rows"][0]["system_id64"] == "121569805492"
    assert state["powers"] == list(POWERS)


def test_others_search_end_to_end(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.api_post("/api/powerplay", {"power": LYR, "mode": "others"})
    _pump_until(p, lambda: p.powerplay.status == "done")
    want = "Li Yong-Rui is present in 3,206 systems another power holds: the 10 nearest."
    assert p.powerplay.message == want
    assert p.ui.powerplay.rows[0].distance_ly == 0.0


def test_far_answer_says_nothing_is_closer(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    """Searched deep out (where the nearest system is ~1,750 ly away): the answer says so."""
    p, _t = plugin
    p.on_journal({"event": "FSDJump", "StarSystem": "Synuefue MO-T c6-0", "SystemAddress": 1,
                  "StarPos": [-1823.56, -63.34, -780.16]}, None)  # fmt: skip
    p.api_post("/api/powerplay", {"power": LYR, "mode": "acquire"})
    _pump_until(p, lambda: p.powerplay.status == "done")
    assert p.powerplay.message.endswith(" ly from where you searched.")
    assert "None is closer than 1," in p.powerplay.message


def test_pledge_reaches_the_page(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.on_journal({"event": "Powerplay", "Power": LYR, "Rank": 50, "Merits": 1}, None)
    p.pump()
    assert json.loads(p.snapshot)["state"]["pledged"] == LYR


def test_powerplay_failure_is_shown(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    t.steps = [Response(500, b"{}")]  # Spansh down
    p.api_post("/api/powerplay", {"power": LYR, "mode": "held"})
    _pump_until(p, lambda: p.powerplay.status == "error", timeout=30)
    assert p.powerplay.message == "Spansh: the site is down or overloaded (HTTP 500); try again later."
    assert p.powerplay.handled == 1


def test_bad_powerplay_request_is_refused(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    reply = p.api_post("/api/powerplay", {"power": "x", "mode": "held"})
    assert reply is not None and reply[0] == 400


def test_powerplay_search_needs_a_position(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.api_post("/api/powerplay", {"power": LYR, "mode": "held"})
    p.pump()
    assert p.powerplay.status == "error" and "jump once" in p.powerplay.message


def test_effects_follow_the_pledge(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.on_journal({"event": "Powerplay", "Power": LYR, "Rank": 50, "Merits": 1}, None)
    got: dict[str, str] = {}
    for n, mode in enumerate(("held", "others", "acquire"), 1):
        p.api_post("/api/powerplay", {"power": LYR, "mode": mode})
        _pump_until(p, lambda n=n: p.powerplay.status == "done" and p.powerplay.handled == n)  # type: ignore[misc]
        got |= {r["system"]: r["effect"] for r in json.loads(p.snapshot)["state"]["powerplay"]["rows"]}
    assert (got["Sol"], got["Sirius"], got["Stein 2051"]) == ("undermine", "reinforce", "acquire")
