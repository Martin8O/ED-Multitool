"""The Missions tab: open missions from the journal — where, until when, how far."""

import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from edmultitool.catalog.missions import mission_kind
from edmultitool.core.events import (
    DockedAt,
    MissionEnded,
    MissionRedirected,
    MissionsListed,
    MissionTaken,
    parse_mission,
)
from edmultitool.core.missions import Missions, ui_missions
from edmultitool.history import scan_missions
from edmultitool.plugin import MISSIONS_JOB, TICK_SECONDS, ScoutPlugin
from edmultitool.sources.http import Request, Response
from edmultitool.worker import JobResult
from fakes import FakeTime, ScriptedTransport, make_client, network_down, reply

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
KEIAN = (-16.0625, 51.125, -32.8125)  # Keian Gu, as the recorded Spansh reply places it


def _e(when: str, event: str, **fields: Any) -> dict[str, Any]:
    return {"timestamp": when, "event": event, **fields}


def _accepted(when: str, mission_id: int, **fields: Any) -> dict[str, Any]:
    base = {
        "Faction": "Hollatja Shared",
        "Name": "Mission_Delivery",
        "LocalisedName": "Deliver 24 units of Superconductors",
        "DestinationSystem": "Keian Gu",
        "DestinationStation": "Hahn Terminal",
        "Expiry": "2026-10-13T15:00:00Z",
        "Wing": False,
        "Reward": 173869,
        "MissionID": mission_id,
    }
    return _e(when, "MissionAccepted", **{**base, **fields})


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("Mission_PassengerBulk_BUSINESS_ARRIVING", "Passengers"),
        ("Mission_PassengerVIP_Explorer_EXPANSION", "VIP passenger"),
        ("Mission_Delivery_name", "Delivery"),
        ("Mission_AltruismCredits_Outbreak", "Donate credits"),
        ("Mission_Assassinate_Planetary_Expansion", "Assassination"),
        ("Mission_HackMegaship", "Hack a megaship"),
        ("Mission_TheNewThing_Boom", "The new thing"),  # not in our list: spelled out
        ("", ""),
    ],
)
def test_mission_kinds_in_plain_words(code: str, kind: str) -> None:
    assert mission_kind(code) == kind


def test_journal_entries_become_mission_events() -> None:
    t = "2026-10-13T10:00:00Z"
    assert parse_mission(_accepted(t, 7)) == MissionTaken(
        7, "Mission_Delivery", "Deliver 24 units of Superconductors", "Hollatja Shared", "Keian Gu",
        "Hahn Terminal", "2026-10-13T15:00:00Z", 173869, False, t,
    )  # fmt: skip
    assert parse_mission(_e(t, "MissionCompleted", MissionID=7, Reward=5)) == MissionEnded(7, "completed", t)
    assert parse_mission(_e(t, "MissionFailed", MissionID=7)) == MissionEnded(7, "failed", t)
    assert parse_mission(_e(t, "MissionAbandoned", MissionID=7)) == MissionEnded(7, "abandoned", t)
    redirect = _e(t, "MissionRedirected", MissionID=7, NewDestinationSystem="Wolf 718",
                  NewDestinationStation="White Station", OldDestinationSystem="LP 339-7")  # fmt: skip
    assert parse_mission(redirect) == MissionRedirected(7, "Wolf 718", "White Station", t)
    listed = _e(t, "Missions", Active=[{"MissionID": 7, "Name": "Mission_Delivery_name", "Expires": 600}],
                Failed=[], Complete=[])  # fmt: skip
    assert parse_mission(listed) == MissionsListed(((7, "Mission_Delivery_name", 600),), t)
    assert parse_mission(_e(t, "Docked", StarSystem="Sol", StationName="Abraham Lincoln")) == DockedAt(
        "Sol", "Abraham Lincoln", t
    )
    assert parse_mission(_e(t, "Location", StarSystem="Sol", Docked=False)) is None
    assert parse_mission(_e(t, "MissionCompleted")) is None  # no id
    assert parse_mission(_e(t, "Music")) is None


def _missions(*entries: dict[str, Any]) -> Missions:
    m = Missions()
    for entry in entries:
        event = parse_mission(entry)
        assert event is not None, entry
        m.add(event)
    return m


def _ui(m: Missions, **kw: Any) -> Any:
    args: dict[str, Any] = {"known": True, "places": {"Keian Gu": KEIAN}, "star_pos": (0.0, 0.0, 0.0),
                            "here": "Sol", "range_ly": 20.0}  # fmt: skip
    return ui_missions(m, NOW, **{**args, **kw})


def test_taken_redirected_and_closed() -> None:
    m = _missions(
        _accepted("2026-10-13T10:00:00Z", 1),
        _accepted("2026-10-13T10:01:00Z", 2, Expiry="2026-10-13T13:00:00Z"),
        _accepted("2026-10-13T10:02:00Z", 3),
        _e("2026-10-13T10:30:00Z", "MissionRedirected", MissionID=2, NewDestinationSystem="Sol",
           NewDestinationStation="Abraham Lincoln", OldDestinationSystem="Keian Gu"),
        _e("2026-10-13T11:00:00Z", "MissionCompleted", MissionID=3),
    )  # fmt: skip
    ui = _ui(m)
    assert [r.id for r in ui.missions] == ["1", "2"]  # soonest to end first; a done job has no limit
    open_, done = ui.missions
    assert (done.done, done.system, done.station, done.here, done.distance_ly, done.jumps) == (
        True, "Sol", "Abraham Lincoln", True, 0.0, 0,
    )  # fmt: skip
    assert (open_.kind, open_.system, open_.distance_ly, open_.jumps) == ("Delivery", "Keian Gu", 62.84, 4)
    assert ui.reward == 2 * 173869 and m.last == "2026-10-13T11:00:00Z"


def test_the_login_list_is_the_truth() -> None:
    """What the game no longer lists ended while we did not look; what it lists that we never saw taken
    still shows, named from its code, its end from the seconds left."""
    m = _missions(
        _accepted("2026-10-12T10:00:00Z", 1),
        _accepted("2026-10-12T10:01:00Z", 2),
        _e("2026-10-13T11:00:00Z", "Missions", Active=[
            {"MissionID": 2, "Name": "Mission_Delivery_name", "Expires": 7200},
            {"MissionID": 9, "Name": "Mission_Courier_Elections_name", "Expires": 3600},
        ]),
    )  # fmt: skip
    assert sorted(m.open) == [2, 9]
    assert m.open[2].expiry == "2026-10-13T13:00:00Z" and m.open[2].title  # known details kept
    unknown = m.open[9]
    assert (unknown.kind, unknown.title, unknown.system, unknown.expiry) == (
        "Courier",
        "",
        "",
        "2026-10-13T12:00:00Z",
    )
    ui = _ui(m)
    assert [r.id for r in ui.missions] == ["2"]  # 9 ends exactly now: ran out
    assert ui.missions[0].distance_ly == 62.84


def test_a_donation_is_handed_in_where_it_was_taken() -> None:
    m = _missions(
        _e("2026-10-13T09:00:00Z", "Docked", StarSystem="Sol", StationName="Abraham Lincoln"),
        _accepted("2026-10-13T09:01:00Z", 4, Name="Mission_AltruismCredits", DestinationSystem=None,
                  DestinationStation=None, Reward=None, Expiry=None),
    )  # fmt: skip
    (row,) = _ui(m).missions
    assert (row.kind, row.system, row.station, row.here, row.reward, row.expiry) == (
        "Donate credits", "Sol", "Abraham Lincoln", True, 0, "",
    )  # fmt: skip


def test_ran_out_leaves_and_no_limit_comes_last() -> None:
    m = _missions(
        _accepted("2026-10-13T09:00:00Z", 1, Expiry="2026-10-13T11:59:59Z"),
        _accepted("2026-10-13T09:01:00Z", 2, Expiry=None),
        _accepted("2026-10-13T09:02:00Z", 3, Expiry="2026-10-14T09:00:00Z"),
    )
    assert [r.id for r in _ui(m).missions] == ["3", "2"]


def test_distance_unknown_without_a_place_or_a_position() -> None:
    m = _missions(_accepted("2026-10-13T09:00:00Z", 1))
    assert (_ui(m, places={}).missions[0].distance_ly, _ui(m, places={}).missions[0].jumps) == (None, None)
    assert _ui(m, star_pos=None).missions[0].distance_ly is None
    assert _ui(m, range_ly=None).missions[0].jumps is None


def test_panel_line() -> None:
    def local(t: datetime) -> datetime:
        return t.astimezone(UTC)  # a fixed zone: the test runs anywhere

    m = _missions(
        _accepted("2026-10-13T09:00:00Z", 1, Expiry="2026-10-13T14:30:00Z", Reward=1_500_000),
        _accepted("2026-10-13T09:01:00Z", 2, Expiry="2026-10-15T09:00:00Z", Reward=900_000),
    )
    assert _ui(m, local=local).text == "2 open · 2.4M Cr · next ends 14:30 (Keian Gu)"
    later = _missions(_accepted("2026-10-13T09:01:00Z", 2, Expiry="2026-10-15T09:00:00Z"))
    assert _ui(later, local=local).text.endswith("next ends Thu 09:00 (Keian Gu)")
    assert _ui(Missions()).text == "none open"
    assert (
        ui_missions(Missions(), NOW, known=False, places={}, star_pos=None, here=None, range_ly=None).text
        == "—"
    )


def _journal(tmp_path: Path, files: dict[str, list[dict[str, Any]]]) -> Path:
    folder = tmp_path / "journal"
    folder.mkdir(exist_ok=True)
    for name, entries in files.items():
        (folder / name).write_text("\n".join(json.dumps(x) for x in entries) + "\n", encoding="utf-8")
    return folder


def test_history_pass_reads_files_in_time_order(tmp_path: Path) -> None:
    # the old name format sorts after the new one by name: the first entry's time decides
    folder = _journal(tmp_path, {
        "Journal.2026-10-13T100000.01.log": [
            _e("2026-10-13T10:00:00Z", "Missions", Active=[{"MissionID": 1, "Name": "Mission_Delivery_name",
                                                            "Expires": 9000}]),
            _e("2026-10-13T10:05:00Z", "MissionCompleted", MissionID=1),
        ],
        "Journal.261012090000.01.log": [
            _accepted("2026-10-12T09:00:00Z", 1),
            _accepted("2026-10-12T09:01:00Z", 2),
        ],
    })  # fmt: skip
    m = scan_missions(folder, threading.Event())
    assert list(m.open) == [] and m.last == "2026-10-13T10:05:00Z"


def _route(request: Request) -> Response:
    if request.url.endswith("/systems/search") and request.json_body is not None:
        return reply("gear_systems_keian")
    return Response(500, b"{}")


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    folder = _journal(tmp_path, {"Journal.2026-10-13T090000.01.log": [_accepted("2026-10-13T09:00:00Z", 1)]})
    p = ScoutPlugin(tmp_path / "plugin", folder)
    p.now = lambda: NOW
    t = ScriptedTransport(_route, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()


def _pump_until(p: ScoutPlugin, done: object, timeout: float = 5) -> None:
    assert callable(done)
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert done()


def _arrive(p: ScoutPlugin, when: str, system: str, address: int, pos: tuple[float, float, float]) -> None:
    p.on_journal(_e(when, "FSDJump", StarSystem=system, SystemAddress=address, StarPos=list(pos)), None)


def test_plugin_reads_the_history_and_places_the_missions(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, t = plugin
    _arrive(p, "2026-10-13T11:00:00Z", "Sol", 10477373803, (0.0, 0.0, 0.0))
    p.on_journal(_accepted("2026-10-13T11:01:00Z", 2, DestinationSystem="Keian Gu"), None)  # taken live
    _pump_until(p, lambda: p._missions_live is None and "Keian Gu" in p.places)
    _pump_until(p, lambda: p.ui.missions.missions and p.ui.missions.missions[0].distance_ly is not None)
    ui = p.ui.missions
    assert ui.known and [r.id for r in ui.missions] == ["1", "2"]
    assert [r.distance_ly for r in ui.missions] == [62.84, 62.84]
    searches = [json.loads(r.json_body or "{}") for r in t.requests if r.url.endswith("/systems/search")]
    assert [q["filters"]["name"]["value"] for q in searches] == [["Keian Gu"]]  # once for both
    state = json.loads(p.snapshot)["state"]
    assert state["panel"]["missions"] == ui.text and ui.text.startswith("2 open · 0.3M Cr · next ends ")
    assert state["missions"]["missions"][0]["system"] == "Keian Gu"
    # arriving there: the place is known from the jump itself, distance 0, nothing asked again
    _arrive(p, "2026-10-13T11:30:00Z", "Keian Gu", 3274652518755, KEIAN)
    assert [(r.here, r.distance_ly, r.jumps) for r in p.ui.missions.missions] == [(True, 0.0, 0)] * 2
    p.on_journal(_e("2026-10-13T11:31:00Z", "MissionCompleted", MissionID=1), None)
    assert [r.id for r in p.ui.missions.missions] == ["2"]
    p.pump()
    assert len([r for r in t.requests if r.url.endswith("/systems/search")]) == 1


def test_spansh_down_keeps_the_missions(tmp_path: Path) -> None:
    folder = _journal(tmp_path, {"Journal.2026-10-13T090000.01.log": [_accepted("2026-10-13T09:00:00Z", 1)]})
    p = ScoutPlugin(tmp_path / "plugin", folder)
    p.now = lambda: NOW
    t = ScriptedTransport(network_down(), repeat_last=True)
    p.client = make_client(t, FakeTime(), max_retries=0)
    p.start_worker()
    try:
        _pump_until(p, lambda: bool(p.ui.missions.lookup))
        ui = p.ui.missions
        assert [r.id for r in ui.missions] == ["1"] and ui.missions[0].distance_ly is None
        assert ui.lookup.startswith("Spansh")
        asked = len(t.requests)
        p.pump()
        assert len(t.requests) == asked  # not asked again until you jump
    finally:
        p.stop_worker()


def test_live_entries_while_the_pass_runs_are_applied_once(tmp_path: Path) -> None:
    history = [
        _accepted("2026-10-13T09:00:00Z", 1),
        _e("2026-10-13T09:10:00Z", "MissionCompleted", MissionID=1),
    ]
    p = ScoutPlugin(tmp_path / "plugin")
    p.now = lambda: NOW
    for entry in history:  # EDMC delivered these while the pass was reading them too
        p.on_journal(entry, None)
    p.on_journal(_accepted("2026-10-13T09:20:00Z", 2), None)
    assert not p.ui.missions.known
    found = scan_missions(
        _journal(tmp_path, {"Journal.2026-10-13T090000.01.log": history}), threading.Event()
    )
    p._finish(JobResult(1, MISSIONS_JOB, found))
    p._refresh()
    assert sorted(p.missions.open) == [2] and p.ui.missions.known


def test_a_done_job_no_longer_runs_out() -> None:
    """Real journals: an assassination redirected before its end stayed open days after it, the game listing
    it with 0 seconds left (no limit) until it was handed in."""
    m = _missions(
        _accepted("2026-10-13T09:00:00Z", 1, Expiry="2026-10-13T11:00:00Z"),
        _e("2026-10-13T09:30:00Z", "MissionRedirected", MissionID=1, NewDestinationSystem="Keian Gu",
           NewDestinationStation="Hahn Terminal"),
    )  # fmt: skip
    (row,) = _ui(m).missions  # past its end at NOW
    assert (row.done, row.expiry) == (True, "")
    m.add(MissionsListed(((1, "Mission_Delivery_name", 0),), "2026-10-13T11:30:00Z"))
    assert m.open[1].expiry == "" and m.open[1].redirected


def test_a_login_that_leaves_a_mission_out_does_not_lose_it() -> None:
    """Real journals: one login listed another mission only; the next listed yours again."""
    m = _missions(_accepted("2026-10-13T09:00:00Z", 1))
    m.add(MissionsListed(((2, "Tactical Takedown", 0),), "2026-10-13T10:00:00Z"))
    assert list(m.open) == [2] and m.open[2].kind == "Tactical takedown"
    m.add(MissionsListed(((1, "Mission_Delivery_name", 3600),), "2026-10-13T10:30:00Z"))
    assert list(m.open) == [1] and m.open[1].title == "Deliver 24 units of Superconductors"
    assert m.open[1].system == "Keian Gu" and m.open[1].expiry == "2026-10-13T11:30:00Z"


def test_a_system_spansh_does_not_know_is_named(tmp_path: Path) -> None:
    folder = _journal(tmp_path, {"Journal.2026-10-13T090000.01.log": [
        _accepted("2026-10-13T09:00:00Z", 1, DestinationSystem="Nowhere Sector AA-A a0"),
    ]})  # fmt: skip
    p = ScoutPlugin(tmp_path / "plugin", folder)
    p.now = lambda: NOW
    empty = Response(200, json.dumps({"count": 0, "from": 0, "results": []}).encode())
    t = ScriptedTransport(empty, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    try:
        _pump_until(p, lambda: bool(p.ui.missions.lookup))
        assert p.ui.missions.lookup == "Spansh does not know Nowhere Sector AA-A a0."
    finally:
        p.stop_worker()


def test_a_bad_expiry_is_no_limit_and_a_list_without_seconds_keeps_the_end() -> None:
    m = _missions(_accepted("2026-10-13T09:00:00Z", 1, Expiry="soon"))
    assert m.open[1].expiry == ""
    m = _missions(_accepted("2026-10-13T09:00:00Z", 1, Expiry="2026-10-13T15:00:00"))  # no zone
    assert m.open[1].expiry == ""
    m = _missions(_accepted("2026-10-13T09:00:00Z", 1))
    m.add(MissionsListed(((1, "Mission_Delivery_name", None),), "2026-10-13T10:00:00Z"))
    assert m.open[1].expiry == "2026-10-13T15:00:00Z"


def test_a_failed_lookup_is_tried_again_after_a_jump_and_case_does_not_matter(tmp_path: Path) -> None:
    folder = _journal(tmp_path, {"Journal.2026-10-13T090000.01.log": [
        _accepted("2026-10-13T09:00:00Z", 1, DestinationSystem="KEIAN GU"),  # Spansh answers "Keian Gu"
    ]})  # fmt: skip
    p = ScoutPlugin(tmp_path / "plugin", folder)
    p.now = lambda: NOW
    t = ScriptedTransport(network_down(), _route, repeat_last=True)
    p.client = make_client(t, FakeTime(), max_retries=0)
    p.start_worker()
    try:
        _pump_until(p, lambda: bool(p.ui.missions.lookup))
        _arrive(p, "2026-10-13T11:00:00Z", "Sol", 10477373803, (0.0, 0.0, 0.0))
        _pump_until(p, lambda: p.ui.missions.missions[0].distance_ly is not None)
        assert p.ui.missions.missions[0].distance_ly == 62.84 and p.ui.missions.lookup == ""
        assert "KEIAN GU" in p.places
        assert len([r for r in t.requests if r.url.endswith("/systems/search")]) == 2
    finally:
        p.stop_worker()


def test_a_mission_running_out_leaves_without_any_journal_entry(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path / "plugin")
    clock = [NOW]
    p.now = lambda: clock[0]
    p._missions_live = None  # the history pass has answered
    p.on_journal(_accepted("2026-10-13T11:00:00Z", 1, Expiry="2026-10-13T12:10:00Z"), None)
    assert p.ui.panel.missions.startswith("1 open")
    clock[0] = NOW + timedelta(minutes=11)
    p._last_tick -= TICK_SECONDS  # the pump's timer has come round
    assert p.pump() is True and p.ui.panel.missions == "none open"


def test_a_failed_missions_pass_says_missions_may_be_missing(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path / "plugin")
    p._finish(JobResult(1, MISSIONS_JOB, None, error="boom"))
    assert "missions taken earlier" in p.notices["history"]
    assert p._missions_live is None
