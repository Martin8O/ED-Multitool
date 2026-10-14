"""The ship is always known: suit fuel is not ship fuel, a Loadout EDMC lost is taken from its state,
every ship is remembered (from journal history too), and the page can plan with a remembered ship.

`journal_onfoot_login.jsonl` = a real login on foot, trimmed, player identity removed."""

import json
import logging
import os
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.events import FleetListed, LoadoutChanged, ShipSold, is_ship, parse_event
from edmultitool.core.fleet import (
    Fleet,
    forget_sold,
    from_doc,
    keep_owned,
    merge_scan,
    remember,
    scan,
    to_doc,
)
from edmultitool.core.settings import Settings
from edmultitool.core.state import GameState, apply, plan_state
from edmultitool.core.view import ui_fleet, ui_plan
from edmultitool.history import scan_history
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources import spansh
from fakes import FakeTime, ScriptedTransport, make_client
from test_plot import MAIA, ROUTE_ONLY, _plotter
from test_scout_api import _pump_until, _sample

DATA = Path(__file__).parent / "data"
LOGIN = [json.loads(line) for line in (DATA / "journal_onfoot_login.jsonl").read_text("utf-8").splitlines()]
LOADOUTS: list[dict[str, Any]] = json.loads((DATA / "loadouts.json").read_text("utf-8"))
LYNX = next(e for e in LOGIN if e["event"] == "Loadout")  # ShipID 1, "XC-07Q", FSD 5A SCO
SIDEWINDER = {**LOADOUTS[2], "ShipID": 0, "ShipIdent": "SW-01", "ShipName": ""}  # 18.30 ly "max"


def _lo(entry: dict[str, Any]) -> LoadoutChanged:
    ev = parse_event(entry)
    assert isinstance(ev, LoadoutChanged)
    return ev


def _edmc_state(loadout: dict[str, Any]) -> dict[str, Any]:
    """EDMC 6.1.2's cumulative state after that Loadout (the ship keys only; monitor.py)."""
    return {
        "ShipID": loadout["ShipID"],
        "ShipIdent": loadout.get("ShipIdent"),
        "ShipName": loadout.get("ShipName"),
        "ShipType": loadout["Ship"].lower(),
        "UnladenMass": loadout["UnladenMass"],
        "CargoCapacity": loadout["CargoCapacity"],
        "MaxJumpRange": loadout["MaxJumpRange"],
        "FuelCapacity": dict(loadout["FuelCapacity"]),
        "Modules": {m["Slot"]: m for m in loadout["Modules"]},
        "OnFoot": False,
    }


# --- suit, SRV, taxi ------------------------------------------------------------------------------------
def test_suits_srvs_fighters_and_taxis_are_not_ships() -> None:
    suits = ("ExplorationSuit_Class1", "FlightSuit", "UtilitySuit_Class3", "TacticalSuit_Class5")
    for symbol in (*suits, "testbuggy", "combat_multicrew_srv_01", "independent_fighter", "adder_taxi", ""):
        assert not is_ship(symbol), symbol
    for symbol in ("mediumtransport01", "SideWinder", "anaconda", "type9_military"):
        assert is_ship(symbol), symbol
    assert parse_event(LOGIN[1]) is None  # LoadGame on foot: Artemis suit, "FuelLevel": 1.0
    assert parse_event({**LYNX, "Ship": "adder_taxi"}) is None


def test_on_foot_login_then_embark_gives_the_ship_and_its_real_range(tmp_path: Path) -> None:
    """Regression: the suit's 1 t "fuel" once stuck to the Lynx: range now 12.04 ly until the refuel."""
    p = ScoutPlugin(tmp_path)
    for entry in LOGIN[:4]:  # Fileheader, LoadGame (suit), Location (on foot), SuitLoadout
        p.on_journal(entry, None)
    assert p.state.ship is None and p.state.fuel_main is None
    assert p.ui.system == "Trianguli Sector EL-Y c7"
    p.on_journal(LOGIN[4], None)  # Embark
    p.on_journal(LOGIN[5], None)  # Loadout
    assert p.ui.panel.ship == "Lynx Highliner · FSD 5A SCO"
    assert p.ui.ranges.now == p.ui.ranges.full == 22.38  # fuel unknown → full tanks, not the suit's 1 t
    p.on_journal(LOGIN[6], None)  # Cargo: 8 t
    assert (p.ui.ranges.now, p.ui.ranges.full) == (22.09, 22.09)
    stored = p.fleet.get(1)
    assert stored is not None and stored.loadout.ship_ident == "XC-07Q" and stored.seen == LYNX["timestamp"]


# --- a Loadout EDMC lost -----------------------------------------------------------------------------
def test_lost_loadout_is_taken_from_edmc_state(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """EDMC's ship-loadout export failed on the Loadout: plugins never got it, but the next entry carries
    EDMC's state with the ship (EDMC 6.1.2 updates its state before the export)."""
    caplog.set_level(logging.INFO)
    p = ScoutPlugin(tmp_path)
    for entry in LOGIN[:5]:
        p.on_journal(entry, {"OnFoot": True})  # no ship in EDMC's state either: EDMC started on foot
    assert p.state.ship is None
    st = _edmc_state(LYNX)
    p.on_journal(LOGIN[6], st)  # Cargo — the Loadout before it never arrived
    assert p.state.ship is not None and p.state.ship.ship_id == 1
    assert p.ui.panel.ship == "Lynx Highliner · FSD 5A SCO"
    assert (p.ui.ranges.now, p.ui.ranges.full) == (22.09, 22.09)
    assert p.state.ship == _lo(LYNX)  # exactly the build the journal Loadout carries
    assert "ship taken from EDMC's state" in caplog.text
    saved = json.loads((tmp_path / "data" / "ships.json").read_text("utf-8"))
    (row,) = saved["ships"]
    assert row["ship_id"] == 1 and row["seen"] == LOGIN[6]["timestamp"]
    before = replace(p.ui, earnings=None, panel=replace(p.ui.panel, earned=""))
    # Same ship in EDMC's state: nothing re-applied; the refuel changes nothing either (unknown fuel
    # already counts as full tanks) but its cost on the Earnings tab.
    p.on_journal(LOGIN[7], st)
    assert p.state.ship == _lo(LYNX)
    assert replace(p.ui, earnings=None, panel=replace(p.ui.panel, earned="")) == before


def test_shipyard_swap_waits_for_the_next_loadout(tmp_path: Path) -> None:
    """After a swap EDMC clears Modules until the Loadout: nothing half-known is taken from it."""
    p = ScoutPlugin(tmp_path)
    p.on_journal(LYNX, None)
    swapped = {**_edmc_state(LYNX), "ShipID": 0, "ShipType": "sidewinder", "Modules": None}
    p.on_journal({"event": "ShipyardSwap", "ShipType": "sidewinder", "ShipID": 0}, swapped)
    assert p.state.ship is not None and p.state.ship.ship_id == 1
    p.on_journal({"event": "Music"}, _edmc_state(SIDEWINDER))  # its Loadout was lost too
    assert p.state.ship is not None and p.state.ship.ship_id == 0


def test_refit_of_the_same_ship_is_noticed(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.on_journal(LYNX, None)
    heavier = {**LYNX, "UnladenMass": 600.0, "MaxJumpRange": 22.5}
    p.on_journal({"event": "Music"}, _edmc_state(heavier))
    assert p.state.ship is not None and p.state.ship.unladen_mass == 600.0


# --- remembering the fleet ------------------------------------------------------------------------------
def test_logging_in_on_foot_shows_the_ship_flown_last(tmp_path: Path) -> None:
    first = ScoutPlugin(tmp_path)
    first.on_journal({**SIDEWINDER, "timestamp": "2026-10-11T10:00:00Z"}, None)
    first.on_journal(LYNX, None)
    again = ScoutPlugin(tmp_path)  # EDMC restarted
    assert again.state.ship is not None and again.state.ship.ship_id == 1
    for entry in LOGIN[:4]:  # suit login: the remembered Lynx stays, with its full-tank range
        again.on_journal(entry, None)
    assert again.ui.panel.ship == "Lynx Highliner · FSD 5A SCO" and again.ui.ranges.now == 22.38
    assert [s.id for s in again.ui.fleet] == ["1", "0"] and [s.current for s in again.ui.fleet] == [
        True,
        False,
    ]


def test_sales_and_fleet_lists_forget_ships(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.on_journal({**SIDEWINDER, "timestamp": "2026-10-11T10:00:00Z"}, None)
    p.on_journal({**LYNX, "ShipID": 5, "timestamp": "2026-10-11T11:00:00Z"}, None)
    p.on_journal(LYNX, None)  # in ship 1 now
    p.on_journal({"timestamp": "2026-10-12T08:00:00Z", "event": "ShipyardSell", "SellShipID": 5}, None)
    assert sorted(p.fleet.ships) == [0, 1]
    listed = {"timestamp": "2026-10-12T08:01:00Z", "event": "StoredShips", "ShipsHere": [], "ShipsRemote": []}
    p.on_journal(listed, None)
    assert sorted(p.fleet.ships) == [1]  # the Sidewinder is gone; the current ship is never on the list


def test_newer_build_wins_and_an_old_sale_does_not_remove_a_new_ship() -> None:
    old, new = _lo({**LYNX, "UnladenMass": 500.0}), _lo(LYNX)
    f = remember(remember(Fleet(), new, "2026-10-12T00:00:00Z"), old, "2026-10-01T00:00:00Z")
    assert f.ships[1].loadout == new
    assert forget_sold(f, 1, "2026-10-05T00:00:00Z") is f  # sold before this build: a later ship, same id
    assert forget_sold(f, 1, "2026-10-13T00:00:00Z").ships == {}
    assert remember(f, new, "2026-10-12T00:00:00Z") is f  # nothing new: same object, no file write
    assert keep_owned(f, frozenset(), 1, "2026-10-13T00:00:00Z") is f


def _entry(when: str, event: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": when, **event}


def _stop_here() -> Iterator[dict[str, Any]]:
    raise AssertionError("read further back than needed")
    yield {}


def test_history_scan_finds_every_owned_ship_and_stops_early() -> None:
    """A real history in short: Sidewinder (0) and Lynx (1), fleet list while in the Lynx."""
    newest_first = [
        _entry("2026-10-12T07:16:18Z", LYNX),
        _entry(
            "2026-09-28T15:07:19Z", {"event": "StoredShips", "ShipsHere": [{"ShipID": 0}], "ShipsRemote": []}
        ),
        _entry("2026-09-28T15:03:38Z", {**LYNX, "UnladenMass": 532.0}),
        _entry("2026-09-27T22:24:02Z", SIDEWINDER),
    ]
    found = scan(_chain(newest_first, _stop_here()))
    assert sorted(found.builds) == [0, 1] and found.owned == frozenset({0, 1})
    assert found.builds[1].loadout.unladen_mass == LYNX["UnladenMass"]  # the newest build, not the older one
    assert found.builds[0].seen == "2026-09-27T22:24:02Z"


def _chain(first: list[dict[str, Any]], then: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    yield from first
    yield from then


def test_history_scan_sales_reused_ids_and_ships_bought_after_the_list() -> None:
    newest_first = [
        _entry("2026-10-12T10:00:00Z", {**LYNX, "ShipID": 7}),  # bought after the list
        _entry("2026-10-11T10:00:00Z", {"event": "ShipyardBuy", "ShipType": "type6"}),
        _entry("2026-10-10T10:00:00Z", {"event": "StoredShips", "ShipsHere": [{"ShipID": 0}, {"ShipID": 3}]}),
        _entry("2026-10-09T10:00:00Z", {"event": "SellShipOnRebuy", "SellShipId": 3}),  # older than the list
        _entry("2026-10-08T10:00:00Z", {**LYNX, "ShipID": 1}),  # in use at the list
        _entry("2026-10-07T10:00:00Z", {"event": "ShipyardSell", "SellShipID": 0}),  # old ship 0 sold ...
        _entry("2026-10-06T10:00:00Z", {**SIDEWINDER, "UnladenMass": 99.0}),  # ... so this build is not it
        _entry("2026-10-05T10:00:00Z", {**SIDEWINDER, "ShipID": 3}),
    ]
    found = scan(_chain(newest_first, _stop_here()))  # every owned ship settled: no need to read on
    assert found.owned == frozenset({0, 1, 3, 7})  # ship 3 was sold before the list: a new ship 3
    assert sorted(found.builds) == [1, 7]  # ships 0 and 3: their only builds predate a sale (reused ids)
    assert found.sold == {0: "2026-10-07T10:00:00Z", 3: "2026-10-09T10:00:00Z"}


def test_history_scan_without_a_fleet_list_keeps_unsold_ships() -> None:
    found = scan([
        _entry("2026-10-12T00:00:00Z", {"event": "ShipyardSell", "SellShipID": 0}),
        _entry("2026-10-11T00:00:00Z", LYNX),
        _entry("2026-10-10T00:00:00Z", SIDEWINDER),
    ])  # fmt: skip
    assert found.owned is None and sorted(found.builds) == [1]


def test_history_files_newest_first_read_only_and_incremental(tmp_path: Path) -> None:
    older = tmp_path / "Journal.2026-10-11T120000.01.log"
    newer = tmp_path / "Journal.2026-10-12T091312.01.log"
    older.write_text(json.dumps(_entry("2026-10-11T12:00:00Z", SIDEWINDER)) + "\n", encoding="utf-8")
    lines = [json.dumps(e) for e in LOGIN] + [
        '{"timestamp":"2026-10-12T07:21:00Z","event":"Loa'
    ]  # half a line
    newer.write_text("\n".join(lines), encoding="utf-8")
    os.utime(older, (1_000, 1_000))
    os.utime(newer, (2_000, 2_000))
    stamp = {p: p.stat().st_mtime_ns for p in (older, newer)}
    found, newest = scan_history(tmp_path, 0.0, threading.Event())
    assert newest == 2_000 and sorted(found.builds) == [0, 1]
    assert {p: p.stat().st_mtime_ns for p in (older, newer)} == stamp  # untouched
    again, newest2 = scan_history(tmp_path, 2_000, threading.Event())
    assert again.builds == {} and newest2 == 2_000  # nothing newer: nothing read
    fleet = merge_scan(Fleet(), found, newest)
    assert (
        fleet.scanned_mtime == 2_000
        and fleet.last_flown is not None
        and fleet.last_flown.loadout.ship_id == 1
    )


def test_plugin_learns_the_fleet_from_history_on_the_worker(tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    journals.mkdir()
    (journals / "Journal.2026-10-12T091312.01.log").write_text(
        "\n".join(json.dumps(e) for e in [_entry("2026-10-11T12:00:00Z", SIDEWINDER), *LOGIN]),
        encoding="utf-8",
    )
    p = ScoutPlugin(tmp_path, journal_dir=journals)
    assert p.state.ship is None
    p.start_worker()
    try:
        for _ in range(500):
            p.pump()
            if p.fleet.ships:
                break
            threading.Event().wait(0.01)
    finally:
        p.stop_worker()
    assert sorted(p.fleet.ships) == [0, 1]
    assert p.state.ship is not None and p.state.ship.ship_id == 1  # the ship flown last
    saved = from_doc(json.loads((tmp_path / "data" / "ships.json").read_text("utf-8")))
    assert saved == p.fleet and saved.scanned_mtime > 0


def test_fleet_file_round_trip_and_damage(tmp_path: Path) -> None:
    f = remember(remember(Fleet(), _lo(SIDEWINDER), "a"), _lo(LYNX), "b")
    f = replace(f, scanned_mtime=123.5)
    doc = to_doc(f)
    assert from_doc(json.loads(json.dumps(doc))) == f
    doc["ships"][0]["unladen_mass"] = "heavy"  # one damaged row: the other ship survives
    assert sorted(from_doc(doc).ships) == [0]
    assert from_doc({**doc, "version": 99}) == Fleet() and from_doc("junk") == Fleet()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "ships.json").write_text("{broken", encoding="utf-8")
    assert ScoutPlugin(tmp_path).fleet == Fleet()  # unreadable → empty, history is read again


# --- planning with a remembered ship -------------------------------------------------------------------
def _two_ships(tmp_path: Path) -> ScoutPlugin:
    p = ScoutPlugin(tmp_path)
    p.on_journal({**SIDEWINDER, "timestamp": "2026-10-11T10:00:00Z"}, None)
    for entry in LOGIN[:7]:  # in the Lynx with 8 t cargo
        p.on_journal(entry, None)
    return p


def test_picker_lists_ships_and_plans_with_the_picked_one(tmp_path: Path) -> None:
    p = _two_ships(tmp_path)
    labels = [(s.id, s.label, s.fsd, s.full, s.current) for s in p.ui.fleet]
    assert labels == [
        ("1", "Lynx Highliner · XC-07Q", "5A SCO", 22.38, True),  # empty hold: same footing for all
        ("0", "Sidewinder · SW-01", "2A", 17.71, False),  # full tanks: below its 18.30 "max"
    ]
    assert p.ui.plan.ship_id is None and p.ui.plan.routing == p.ui.ranges.routing
    assert p.api_post("/api/plan-ship", {"ship_id": "0"}) == (202, {"accepted": "plan-ship"})
    p.pump()
    plan = json.loads(p.snapshot)["state"]["plan"]
    assert plan["ship_id"] == "0" and plan["ship"] == "Sidewinder · SW-01"
    side = GameState(ship=_lo(SIDEWINDER))
    assert plan["full"] == round(side.full_tank_range() or 0, 2) == 17.71
    margin = 1 - p.settings.margin_pct / 100
    assert plan["routing"] == round((side.full_tank_range() or 0) * margin, 2)
    assert plan["text"].endswith(" · Sidewinder · SW-01")
    assert p.ui.ranges.routing != plan["routing"] and p.ui.panel.ship.startswith("Lynx")  # panel: your ship
    assert p.scout.handled == 0  # a pick is not a scout command
    p.api_post("/api/plan-ship", {"ship_id": None})
    p.pump()
    assert p.ui.plan.ship_id is None


@pytest.mark.parametrize(
    ("body", "status"),
    [({"ship_id": 0}, 400), ({"ship_id": "-1"}, 400), ({"ship_id": "1e3"}, 400), ({}, 400), ([], 400)],
)
def test_bad_picks_are_refused(tmp_path: Path, body: Any, status: int) -> None:
    p = ScoutPlugin(tmp_path)
    got = p.api_post("/api/plan-ship", body)
    assert got is not None and got[0] == status
    assert not p.pump()


def test_unknown_or_current_pick_means_the_ship_you_sit_in(tmp_path: Path) -> None:
    p = _two_ships(tmp_path)
    for raw in ("42", "1"):
        p.api_post("/api/plan-ship", {"ship_id": raw})
        p.pump()
        assert p.plan_pick is None and p.ui.plan.ship_id is None


def test_boarding_or_selling_the_picked_ship_ends_the_pick(tmp_path: Path) -> None:
    p = _two_ships(tmp_path)
    p._pick_ship(0)
    p.on_journal({**SIDEWINDER, "timestamp": "2026-10-12T09:00:00Z"}, None)  # you board it
    assert p.plan_pick is None
    p.on_journal({**LYNX, "timestamp": "2026-10-12T09:10:00Z"}, None)
    p._pick_ship(0)
    p.on_journal({"timestamp": "2026-10-12T09:20:00Z", "event": "ShipyardSell", "SellShipID": 0}, None)
    assert p.plan_pick is None and 0 not in p.fleet.ships


def test_pick_is_not_remembered_across_restarts(tmp_path: Path) -> None:
    p = _two_ships(tmp_path)
    p._pick_ship(0)
    assert ScoutPlugin(tmp_path).plan_pick is None  # always the ship you sit in


def test_plan_state_uses_the_picked_build_with_full_tanks_and_no_cargo() -> None:
    here = apply(apply(GameState(), _lo(LYNX)), parse_event(LOGIN[6]))  # type: ignore[arg-type]
    fleet = remember(remember(Fleet(), _lo(LYNX), "b"), _lo(SIDEWINDER), "a")
    assert plan_state(here, fleet, None) is here and plan_state(here, fleet, 1) is here
    side = plan_state(here, fleet, 0)
    assert side.ship == _lo(SIDEWINDER) and side.cargo == 0 and side.fuel_main is None
    plan = ui_plan(here, Settings(margin_pct=0), fleet, 0)
    assert plan.routing == plan.full == 17.71
    assert [s.current for s in ui_fleet(fleet, here)] == [True, False]


def test_route_only_plot_uses_the_picked_ship(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    for entry in _sample():
        p.on_journal(entry, None)
    p._set_fleet(remember(p.fleet, _lo({**SIDEWINDER, "ShipID": 99}), "2000-01-01T00:00:00Z"))  # parked
    assert p.state.ship is not None and p.state.ship.ship_id != 99
    t = ScriptedTransport(_plotter, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    try:
        p.api_post("/api/plan-ship", {"ship_id": "99"})
        p.api_post("/api/scout", {**ROUTE_ONLY, "from_system": "", "to_system": "maia", "plotter": "exact"})
        _pump_until(p, "done")
    finally:
        p.stop_worker()
    (post,) = [r for r in t.requests if r.method == "POST"]
    form = dict(post.form or ())
    side = _lo({**SIDEWINDER, "ShipID": 99})
    assert side.fsd is not None and form["optimal_mass"] == spansh._dec(side.fsd.spec.opt_mass)
    assert form["cargo"] == "0" and form["destination"] == str(MAIA)
    route = p.scout.route
    assert route is not None and route.range_ly == p.ui.plan.full == 17.71


def test_live_fleet_events_parse() -> None:
    assert parse_event({"event": "ShipyardBuy", "ShipType": "x"}) is None  # nothing traded in
    assert parse_event({"event": "ShipyardBuy", "SellShipID": 4}) == ShipSold(4)
    listed = parse_event(
        {"event": "StoredShips", "ShipsHere": [{"ShipID": 2}], "ShipsRemote": [{"ShipID": 9}, {}]}
    )
    assert listed == FleetListed(frozenset({2, 9}))


def test_later_pass_with_a_fleet_list_but_no_older_loadout_keeps_the_ship_flown_last() -> None:
    """Login on foot, shipyard visit, log out: the newer file has a fleet list (which never names the ship in
    use) and no Loadout. Nothing may be pruned from that — the Lynx stays."""
    fleet = remember(remember(Fleet(), _lo(SIDEWINDER), "2026-10-01T00:00:00Z"), _lo(LYNX), LYNX["timestamp"])
    fleet = replace(fleet, scanned_mtime=1_000.0)
    newer = [
        _entry(
            "2026-10-13T08:01:00Z", {"event": "StoredShips", "ShipsHere": [{"ShipID": 0}], "ShipsRemote": []}
        ),
        LOGIN[1],  # LoadGame on foot
    ]
    found = scan(newer)
    assert found.owned is None and found.builds == {}
    merged = merge_scan(fleet, found, 2_000.0)
    assert sorted(merged.ships) == [0, 1] and merged.last_flown is not None
    assert merged.last_flown.loadout.ship_id == 1 and merged.scanned_mtime == 2_000.0
    assert merge_scan(merged, scan([]), 2_000.0) is merged  # nothing new: no rewrite of the file


def test_non_ascii_digits_are_refused(tmp_path: Path) -> None:
    got = ScoutPlugin(tmp_path).api_post("/api/plan-ship", {"ship_id": "²"})
    assert got is not None and got[0] == 400
