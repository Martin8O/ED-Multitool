"""What you explored already — journal events, the store's rules, the history pass, route
building with done bodies, swapping a spent stop, and the plugin end to end."""

import copy
import json
import logging
import math
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, replace
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.events import (
    BodyMapped,
    Died,
    ExplorationSold,
    Killer,
    OrganicSold,
    SpeciesAnalysed,
    parse_event,
)
from edmultitool.core.explored import NOTHING, Explored, Located, body_id64
from edmultitool.core.route import build_route, spent, swap_spent
from edmultitool.core.scout import BioSpecies, ScoutBody, ScoutQuery, ScoutResult, ScoutSystem
from edmultitool.core.settings import ScoutPrefs, parse_scout_prefs
from edmultitool.core.view import regrade, route_from_doc, ui_route
from edmultitool.explored import ExploredStore
from edmultitool.history import scan_explored
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources.http import Request, Response
from edmultitool.sources.spansh import _systems  # the adapter's own reader, for the recorded reply
from fakes import FakeTime, ScriptedTransport, make_client, recorded
from test_scout_api import PREFS

LOG = logging.getLogger("test")
T0 = "2026-10-10T16:00:00Z"


def at(minute: int) -> str:
    return f"2026-10-10T16:{minute:02d}:00Z"


# --- journal events -------------------------------------------------------------------------------------
def test_journal_lines_become_explore_events() -> None:
    # shapes copied from real journals (2026-09-26 … 2026-10-11)
    assert parse_event(
        {"timestamp": T0, "event": "SAAScanComplete", "BodyName": "LP 339-7 4 a",
         "SystemAddress": 2868903945649, "BodyID": 16, "ProbesUsed": 2, "EfficiencyTarget": 2}
    ) == BodyMapped(2868903945649, 16, T0)  # fmt: skip
    analyse = {
        "timestamp": T0, "event": "ScanOrganic", "ScanType": "Analyse",
        "Genus": "$Codex_Ent_Fonticulus_Genus_Name;", "Species": "$Codex_Ent_Fonticulus_02_Name;",
        "Species_Localised": "Fonticulua Campestris",
        "SystemAddress": 359066112746, "Body": 28,
    }  # fmt: skip
    assert parse_event(analyse) == SpeciesAnalysed(359066112746, 28, "Fonticulua Campestris", T0)
    assert parse_event({**analyse, "ScanType": "Sample"}) is None  # only the third sample counts
    assert parse_event({**analyse, "ScanType": "Log"}) is None
    assert parse_event(
        {"timestamp": T0, "event": "SellExplorationData", "Systems": ["Wolfsegen"], "Discovered": []}
    ) == ExplorationSold(("Wolfsegen",), T0)
    assert parse_event(
        {"timestamp": T0, "event": "MultiSellExplorationData",
         "Discovered": [{"SystemName": "Mekretu", "NumBodies": 2},
                        {"SystemName": "Core Sys Sector UO-R b4-6"}]}
    ) == ExplorationSold(("Mekretu", "Core Sys Sector UO-R b4-6"), T0)  # fmt: skip
    assert parse_event(
        {"timestamp": T0, "event": "SellOrganicData", "MarketID": 1,
         "BioData": [{"Species_Localised": "Tussock Cultro", "Value": 1766600},
                     {"Species_Localised": "Concha Aureolas"}]}
    ) == OrganicSold(("Tussock Cultro", "Concha Aureolas"), T0)  # fmt: skip
    assert parse_event({"timestamp": T0, "event": "Died", "KillerName": "x"}) == Died(
        T0, (Killer("x", "", ""),)
    )
    # broken entries are ignored, never raise
    assert parse_event({"event": "Died"}) is None  # no time: the undo rules could not be applied
    assert (
        parse_event({"timestamp": T0, "event": "SAAScanComplete", "SystemAddress": "1", "BodyID": 2}) is None
    )
    assert parse_event({"timestamp": T0, "event": "MultiSellExplorationData", "Discovered": "x"}) is None


def test_body_id64_matches_spansh() -> None:
    raw = recorded("riches_done")["body"]["result"][1]
    for body in raw["bodies"]:  # Spansh's "id" is the id64 again; the BodyID sits in the top 9 bits
        assert body_id64(int(raw["id64"]), _body_id(body)) == int(body["id64"]) == int(body["id"])


# --- the store ------------------------------------------------------------------------------------------
SYS = 359066112746


def _store(tmp_path: Path) -> ExploredStore:
    return ExploredStore.open(tmp_path / "explored.db", LOG)


def test_mapped_and_analysed_are_remembered_across_restarts(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.apply(
        [(BodyMapped(SYS, 3, at(1)), "Here"), (SpeciesAnalysed(SYS, 4, "Bacterium  Aurasus", at(2)), "Here")]
    )
    assert not s.apply([(BodyMapped(SYS, 3, at(5)), "Here")])  # the first time counts
    s.close()
    snap = _store(tmp_path).snapshot()
    assert snap.is_mapped(body_id64(SYS, 3)) and not snap.is_mapped(body_id64(SYS, 4))
    assert snap.is_analysed(body_id64(SYS, 4), "bacterium aurasus")  # letter case and spaces do not matter


def test_death_forgets_only_unsold_data_from_before_it(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.apply([
        (BodyMapped(SYS, 1, at(1)), "Sold Sys"),
        (BodyMapped(SYS + 1, 2, at(2)), "Unsold Sys"),
        (SpeciesAnalysed(SYS, 5, "Tussock Cultro", at(3)), "Sold Sys"),
        (SpeciesAnalysed(SYS, 6, "Tussock Cultro", at(4)), "Sold Sys"),
        (ExplorationSold(("Sold Sys",), at(10)), ""),
        (OrganicSold(("Tussock Cultro",), at(11)), ""),  # one sample sold: the oldest
        (Died(at(20)), ""),
        (BodyMapped(SYS + 2, 7, at(21)), "After"),
    ])  # fmt: skip
    snap = s.snapshot()
    assert snap.mapped == {body_id64(SYS, 1), body_id64(SYS + 2, 7)}
    assert snap.analysed == {(body_id64(SYS, 5), "tussock cultro")}


def test_reading_a_file_again_changes_nothing(tmp_path: Path) -> None:
    events: list[Located] = [
        (SpeciesAnalysed(SYS, 5, "Tussock Cultro", at(1)), "A"),
        (SpeciesAnalysed(SYS, 6, "Tussock Cultro", at(2)), "A"),
        (OrganicSold(("Tussock Cultro",), at(3)), ""),
        (BodyMapped(SYS, 9, at(4)), "A"),
        (Died(at(5)), ""),
        (BodyMapped(SYS, 8, at(6)), "A"),
    ]
    s = _store(tmp_path)
    s.apply(events)
    first = s.snapshot()
    # The same file again (the newest is read on every start): a sale must not sell a second sample,
    # a death must not forget what came after it.
    s.apply(events)
    assert (
        s.snapshot()
        == first
        == Explored(frozenset({body_id64(SYS, 8)}), frozenset({(body_id64(SYS, 5), "tussock cultro")}))
    )


def test_a_live_death_stays_applied_after_an_older_history_pass(tmp_path: Path) -> None:
    """The plugin applies live events again after the pass result: a death seen live while the pass read
    an older copy of the file must win over the mapping the pass reports from before it."""
    s = _store(tmp_path)
    live = [(Died(at(30)), "")]
    s.apply(live)
    s.apply([(BodyMapped(SYS, 1, at(10)), "A")])  # the pass result arrives
    s.apply(live)
    assert s.snapshot() == NOTHING


def test_a_damaged_store_is_put_aside_and_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "explored.db"
    path.write_bytes(b"this is not a database" * 100)
    s = ExploredStore.open(path, LOG)
    assert s.snapshot() == NOTHING and s.scanned_mtime == 0.0
    assert (tmp_path / "explored.bad").exists()
    s.apply([(BodyMapped(SYS, 1, at(1)), "A")], scanned_mtime=123.5)
    assert s.scanned_mtime == 123.5


# --- history pass ---------------------------------------------------------------------------------------
def _journal(folder: Path, name: str, entries: list[dict[str, Any]], mtime: float) -> Path:
    path = folder / name
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_history_pass_reads_oldest_first_with_the_system_you_were_in(tmp_path: Path) -> None:
    jump = {"event": "FSDJump", "StarSystem": "Here", "SystemAddress": SYS, "StarPos": [1, 2, 3]}
    _journal(tmp_path, "Journal.2026-10-11T100000.01.log", [
        {"timestamp": at(40), "event": "SellExplorationData", "Systems": ["Here"]},
        {"timestamp": at(41), "event": "Died"},
    ], 2000.0)  # fmt: skip
    _journal(tmp_path, "Journal.2026-10-10T100000.01.log", [
        {"timestamp": at(1), **jump},
        {"timestamp": at(2), "event": "SAAScanComplete", "SystemAddress": SYS, "BodyID": 3},
        '{"timestamp": "broken',  # type: ignore[list-item]
    ], 1000.0)  # fmt: skip
    found, newest = scan_explored(tmp_path, 0.0, threading.Event())
    assert newest == 2000.0
    assert found == [
        (BodyMapped(SYS, 3, at(2)), "Here"),
        (ExplorationSold(("Here",), at(40)), "Here"),
        (Died(at(41)), "Here"),
    ]
    later, _ = scan_explored(tmp_path, 1000.0, threading.Event())  # only files written since
    assert [e for e, _ in later] == [ExplorationSold(("Here",), at(40)), Died(at(41))]


# --- route building with done bodies --------------------------------------------------------------------
PREFS_OFF = ScoutPrefs(to_system="B", min_scan=0, min_mapping=0, min_species=0, min_bio_body=0)


def _sys(id64: int, name: str, x: float, *bodies: ScoutBody, y: float = 0.0) -> ScoutSystem:
    return ScoutSystem(id64, name, 1, x, y, 0.0, bodies)


def _body(sys_id: int, n: int, mapping: int = 0, species: tuple[BioSpecies, ...] = ()) -> ScoutBody:
    return ScoutBody(
        body_id64(sys_id, n),
        f"S{sys_id} {n}",
        "Planet",
        "Water world",
        10.0,
        mapping // 4,
        mapping,
        False,
        species,
    )


def _result(kind: str, *systems: ScoutSystem, n: int = 10) -> ScoutResult:
    ends = (_sys(1, "Start", 0.0), _sys(2, "End", 100.0))
    return ScoutResult(kind, ScoutQuery("Start", "End", 20.0, max_results=n), (ends[0], *systems, ends[1]))  # type: ignore[arg-type]


def test_a_mapped_body_is_kept_on_the_stop_but_not_counted() -> None:
    riches = _result("riches", _sys(10, "Ten", 30.0, _body(10, 1, 2_000_000), _body(10, 2, 1_000_000)))
    done = Explored(mapped=frozenset({body_id64(10, 1)}))
    (stop,) = build_route([riches], PREFS_OFF, done).stops
    assert [b.mapped for b in stop.bodies] == [True, False]
    assert stop.mapping_value == 1_000_000 and stop.counted
    (both,) = build_route(
        [riches], PREFS_OFF, Explored(mapped=frozenset({body_id64(10, 1), body_id64(10, 2)}))
    ).stops
    assert not both.counted and both.worth == 0 and len(both.bodies) == 2  # spent, still shown


def test_analysed_species_drop_out_and_the_body_total_is_checked_again() -> None:
    sp = (
        BioSpecies("Tussock", "Tussock Cultro", 3_000_000, 1),
        BioSpecies("Concha", "Concha Aureolas", 600_000, 1),
    )
    bio = _result("bio", _sys(10, "Ten", 30.0, _body(10, 1, species=sp)))
    prefs = replace(PREFS_OFF, min_bio_body=1_000_000)
    done = Explored(analysed=frozenset({(body_id64(10, 1), "tussock cultro")}))
    (stop,) = build_route([bio], prefs, done).stops
    (body,) = stop.bodies
    assert body.done_species == ("Tussock Cultro",) and [s.species for s in body.open_species] == [
        "Concha Aureolas"
    ]
    assert not stop.counted  # 600k left < 1M per body: spent
    (open_stop,) = build_route([bio], PREFS_OFF, done).stops
    assert open_stop.counted and open_stop.bio_value == 600_000


def test_a_spent_stop_is_swapped_only_within_the_detour() -> None:
    spent_a = _sys(10, "Spent A", 30.0, _body(10, 1, 2_000_000))
    spent_b = _sys(11, "Spent B", 70.0, _body(11, 1, 2_000_000))
    near_a = _sys(20, "Near A", 31.0, _body(20, 1, 1_500_000), y=3.0)
    richer_far = _sys(21, "Rich but far", 32.0, _body(21, 1, 9_000_000), y=60.0)
    done = Explored(mapped=frozenset({body_id64(10, 1), body_id64(11, 1)}))
    route = build_route([_result("riches", spent_a, spent_b)], PREFS_OFF, done)
    pool = build_route([_result("riches", spent_a, near_a, richer_far, spent_b, n=30)], PREFS_OFF, done)
    assert spent(route) == 2

    swapped = swap_spent(route, pool, 25.0)
    assert [s.name for s in swapped.stops] == ["Near A", "Spent B"]
    near = swapped.stops[0]
    assert near.replaces == "Spent A" and 0 < near.detour_ly < 1.0
    assert near.leg_ly == pytest.approx((31.0**2 + 3.0**2) ** 0.5)  # legs measured again
    assert not swapped.stops[1].counted  # no stop close enough: kept, marked

    assert swap_spent(route, pool, 0.0) == route  # 0 = never swap
    wide = {s.replaces: s.name for s in swap_spent(route, pool, 200.0).stops}
    assert wide == {"Spent A": "Rich but far", "Spent B": "Near A"}  # the most valuable that fits, once each


# --- what the page shows --------------------------------------------------------------------------------
def test_page_marks_done_bodies_and_follows_changes_both_ways() -> None:
    sp = (BioSpecies("Tussock", "Tussock Cultro", 3_000_000, 1),)
    riches = _result("riches", _sys(10, "Ten", 30.0, _body(10, 1, 40_000_000)))
    bio = _result("bio", _sys(10, "Ten", 30.0, _body(10, 1, 40_000_000, species=sp)))
    ui = ui_route(build_route([riches, bio], PREFS_OFF), "t")
    (stop,) = ui.stops
    assert stop.mapping_value == 40_000_000 and stop.bio_value == 3_000_000 and stop.wealth == 4

    mapped = Explored(mapped=frozenset({body_id64(10, 1)}))
    (stop,) = regrade(ui, mapped).stops
    assert stop.bodies[0].done and stop.mapping_value == 0 and stop.bio_value == 3_000_000
    assert stop.wealth == 1 and not stop.spent

    everything = Explored(mapped=mapped.mapped, analysed=frozenset({(body_id64(10, 1), "tussock cultro")}))
    (stop,) = regrade(ui, everything).stops
    assert stop.spent and stop.wealth == 0 and stop.bodies[0].species[0].done

    (stop,) = regrade(regrade(ui, everything), NOTHING).stops  # after a death: open again
    assert not stop.spent and stop.wealth == 4

    saved = json.loads(json.dumps(asdict(regrade(ui, everything))))
    assert route_from_doc(saved) == regrade(ui, everything)  # done marks survive a restart


def test_form_takes_the_detour_setting() -> None:
    prefs = parse_scout_prefs({**PREFS, "max_detour_ly": 12.5})
    assert isinstance(prefs, ScoutPrefs) and prefs.max_detour_ly == 12.5
    assert isinstance(parse_scout_prefs({**PREFS, "max_detour_ly": -1}), str)
    assert ScoutPrefs().max_detour_ly == 25.0


# --- the plugin end to end ------------------------------------------------------------------------------
RICHES = recorded("riches_done")["body"]


def _pool_reply() -> dict[str, Any]:
    """The recorded Sol → Colonia riches result asked for 30 stops: the same 5 plus two more — one beside
    "Eoch Flyuae BB-N d7-2304", one 400 ly off the line beside "Eol Prou BZ-A d138"."""
    body: dict[str, Any] = copy.deepcopy(RICHES)
    rows = body["result"]

    def extra(beside: dict[str, Any], id64: int, name: str, dx: float, mapping: int) -> dict[str, Any]:
        bid = body_id64(id64, 5)
        b = {**beside["bodies"][0], "id64": str(bid), "id": bid, "name": f"{name} 5",
             "estimated_mapping_value": mapping, "estimated_scan_value": mapping // 4}  # fmt: skip
        return {**beside, "id64": id64, "name": name, "x": beside["x"] + dx, "bodies": [b]}

    bbn = next(r for r in rows if r["name"] == "Eoch Flyuae BB-N d7-2304")
    eol = next(r for r in rows if r["name"] == "Eol Prou BZ-A d138")
    rows.insert(3, extra(bbn, 900001, "Swap In AA-A a1", 2.0, 1_500_000))
    rows.insert(-1, extra(eol, 900002, "Far Away AA-A a2", 400.0, 9_000_000))
    return body


def _spansh_riches(request: Request) -> Response:
    if request.method == "POST":
        _spansh_riches.size = dict(request.form or ())["max_results"]  # type: ignore[attr-defined]
        return Response(202, json.dumps({"job": "J", "status": "queued"}).encode())
    doc = RICHES if _spansh_riches.size == "10" else _pool_reply()  # type: ignore[attr-defined]
    return Response(200, json.dumps(doc).encode())


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path)
    p.on_journal({"timestamp": T0, "event": "Location", "StarSystem": "Sol", "SystemAddress": 10477373803,
                  "StarPos": [0.0, 0.0, 0.0]}, None)  # fmt: skip
    loadout = json.loads(
        (Path(__file__).parent / "data" / "journal_sample.jsonl").read_text("utf-8").splitlines()[2]
    )
    p.on_journal(loadout, None)
    t = ScriptedTransport(_spansh_riches, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()
    p.close()


def _wait(p: ScoutPlugin, status: str) -> None:
    deadline = time.monotonic() + 5
    while p.scout.status != status and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert p.scout.status == status, (p.scout.status, p.scout.message)


def _map_all(p: ScoutPlugin, name: str, minute: int) -> None:
    row = next(r for r in RICHES["result"] if r["name"] == name)
    for b in row["bodies"]:
        p.on_journal({"timestamp": at(minute), "event": "SAAScanComplete", "SystemAddress": int(row["id64"]),
                      "BodyID": _body_id(b), "BodyName": b["name"]}, None)  # fmt: skip


def test_one_stop_swapped_and_one_kept_and_marked(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    assert p.ui.ranges.routing, "the sample journal must give a jump range"
    _map_all(p, "Eoch Flyuae BB-N d7-2304", 1)
    _map_all(p, "Eol Prou BZ-A d138", 2)
    p.api_post("/api/scout", {**PREFS, "bio": False})
    _wait(p, "done")

    sizes = [dict(r.form or ())["max_results"] for r in t.requests if r.method == "POST"]
    assert sizes == ["10", "30"]  # the bigger search only because stops were spent
    stops = json.loads(p.snapshot)["state"]["scout"]["route"]["stops"]
    names = [s["name"] for s in stops]
    assert "Eoch Flyuae BB-N d7-2304" not in names and "Swap In AA-A a1" in names
    swap = next(s for s in stops if s["name"] == "Swap In AA-A a1")
    assert (
        swap["replaces"] == "Eoch Flyuae BB-N d7-2304" and 0 <= swap["detour_ly"] < 5
    )  # a shortcut counts as 0
    kept = next(s for s in stops if s["name"] == "Eol Prou BZ-A d138")
    assert kept["spent"] and kept["wealth"] == 0 and all(b["done"] for b in kept["bodies"])
    assert "Far Away AA-A a2" not in names
    assert not any(s["spent"] for s in stops if s["name"] != "Eol Prou BZ-A d138")


def test_nothing_done_asks_spansh_once_and_live_marks_follow(
    plugin: tuple[ScoutPlugin, ScriptedTransport], tmp_path: Path
) -> None:
    p, t = plugin
    p.api_post("/api/scout", {**PREFS, "bio": False})
    _wait(p, "done")
    assert [dict(r.form or ())["max_results"] for r in t.requests if r.method == "POST"] == ["10"]

    def stop(name: str) -> dict[str, Any]:
        doc = json.loads(p.snapshot)["state"]["scout"]["route"]["stops"]
        return next(s for s in doc if s["name"] == name)

    assert not stop("Eol Prou BZ-A d138")["spent"]
    _map_all(p, "Eol Prou BZ-A d138", 5)  # mapped while following the route: marked at once …
    assert stop("Eol Prou BZ-A d138")["spent"]
    saved = json.loads((tmp_path / "data" / "route.json").read_text(encoding="utf-8"))
    assert all(
        b["done"]
        for b in next(s for s in saved["route"]["stops"] if s["name"] == "Eol Prou BZ-A d138")["bodies"]
    )
    p.on_journal({"timestamp": at(9), "event": "Died"}, None)  # … and open again after a death
    assert not stop("Eol Prou BZ-A d138")["spent"]


def test_history_pass_runs_on_start_and_marks_the_restored_route(tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    journals.mkdir()
    row = next(r for r in RICHES["result"] if r["name"] == "Eol Prou BZ-A d138")
    _journal(journals, "Journal.2026-10-10T100000.01.log", [
        {"timestamp": at(1), "event": "FSDJump", "StarSystem": row["name"], "SystemAddress": int(row["id64"]),
         "StarPos": [row["x"], row["y"], row["z"]]},
        *({"timestamp": at(2), "event": "SAAScanComplete", "SystemAddress": int(row["id64"]),
           "BodyID": _body_id(b)} for b in row["bodies"]),
    ], time.time())  # fmt: skip
    route = ui_route(build_route([_scout_result(RICHES)], ScoutPrefs(to_system="Colonia")), "t")
    (tmp_path / "data").mkdir()
    doc = {"version": 1, "next": 1, "route": asdict(route)}
    (tmp_path / "data" / "route.json").write_text(json.dumps(doc), encoding="utf-8")

    p = ScoutPlugin(tmp_path, journal_dir=journals)
    try:
        assert p.explored.counts() == (0, 0)
        p.client = make_client(ScriptedTransport(), FakeTime())
        p.start_worker()
        deadline = time.monotonic() + 5
        while p.explored.counts() == (0, 0) and time.monotonic() < deadline:
            p.pump()
            time.sleep(0.01)
        assert p.explored.counts() == (2, 0)
        assert p.scout.route is not None
        eol = next(s for s in p.scout.route.stops if s.name == "Eol Prou BZ-A d138")
        assert eol.spent and all(b.done for b in eol.bodies)
        dump = json.loads((tmp_path / "data" / "debug" / "panel.json").read_text(encoding="utf-8"))
        assert dump["explored"]["mapped"] == 2 and dump["explored"]["pass_running"] is False
    finally:
        p.stop_worker()
        p.close()


def _body_id(body: dict[str, Any]) -> int:
    """The journal's BodyID of a Spansh body."""
    return int(body["id64"]) >> 55


def _scout_result(body: dict[str, Any]) -> ScoutResult:
    return ScoutResult("riches", ScoutQuery("Sol", "Colonia", 20.0), _systems(body["result"]))


# --- edge cases: the store's rules at their limits ----------------------------------------
@pytest.mark.parametrize("kind", ["cartographic", "organic"])
def test_a_sale_seen_live_before_the_history_pass_still_counts(tmp_path: Path, kind: str) -> None:
    """Live events are applied again after the pass result; a sale applied before the data it covers
    reached the store must still mark that data sold — and stay correct when the file is read again."""
    s = _store(tmp_path)
    done: Located = (
        (BodyMapped(SYS, 1, at(1)), "X")
        if kind == "cartographic"
        else (SpeciesAnalysed(SYS, 1, "Tussock Cultro", at(1)), "X")
    )
    sale: Located = (
        (ExplorationSold(("X",), at(5)), "")
        if kind == "cartographic"
        else (OrganicSold(("Tussock Cultro",), at(5)), "")
    )
    s.apply([sale])  # live, while the pass runs
    s.apply([done, sale])  # the pass result (it read the sale too) …
    s.apply([sale])  # … then the live events again
    s.apply([done, sale])  # and the newest file read again on the next start
    s.apply([(Died(at(9)), "")])
    assert s.counts() == ((1, 0) if kind == "cartographic" else (0, 1))  # sold before the death: kept


def test_one_organic_sale_sells_one_sample_however_often_it_is_read(tmp_path: Path) -> None:
    s = _store(tmp_path)
    two = [(SpeciesAnalysed(SYS, b, "Tussock Cultro", at(b)), "X") for b in (1, 2)]
    sale: Located = (OrganicSold(("Tussock Cultro",), at(5)), "")
    for _ in range(3):
        s.apply([*two, sale])
    s.apply([(Died(at(9)), "")])
    assert s.snapshot().analysed == {(body_id64(SYS, 1), "tussock cultro")}  # the oldest one was sold


def test_page_and_route_builder_agree_on_the_bio_minimum() -> None:
    sp = (
        BioSpecies("Tussock", "Tussock Cultro", 3_000_000, 1),
        BioSpecies("Concha", "Concha Aureolas", 600_000, 1),
    )
    bio = _result("bio", _sys(10, "Ten", 30.0, _body(10, 1, species=sp)))
    prefs = replace(PREFS_OFF, min_bio_body=1_000_000)
    done = Explored(analysed=frozenset({(body_id64(10, 1), "tussock cultro")}))
    built = build_route([bio], prefs, done)
    (stop,) = ui_route(built, "t").stops
    assert spent(built) == 1 and stop.spent and stop.bio_value == 0 and stop.wealth == 0
    (live,) = regrade(ui_route(build_route([bio], prefs), "t"), done).stops  # analysed while following
    assert live.spent and live.bodies[0].bio_value == 0
    (open_,) = ui_route(build_route([bio], PREFS_OFF, done), "t").stops  # no minimum: 600k left counts
    assert not open_.spent and open_.bio_value == 600_000


def test_a_route_saved_before_b3_loads_and_is_graded() -> None:
    ui = ui_route(build_route([_scout_result(RICHES)], ScoutPrefs(to_system="Colonia")), "t")
    doc = asdict(ui)
    for key in ("min_bio_body",):
        doc.pop(key)
    for stop in doc["stops"]:
        for key in ("replaces", "detour_ly", "spent"):
            stop.pop(key)
        for body in stop["bodies"]:
            body.pop("done")
            for sp in body["species"]:
                sp.pop("done")
    assert route_from_doc(doc) == replace(ui, min_bio_body=0)  # unknown minimum: every species left counts


def test_the_detour_is_what_the_whole_trip_gains() -> None:
    """A replacement past the next stop is visited in its order along the line: the detour shown is the
    real change of the trip, not a guess between the old neighbours."""
    a = _sys(10, "A", 30.0, _body(10, 1, 2_000_000))
    spent_s = _sys(11, "Spent", 40.0, _body(11, 1, 2_000_000))
    b = _sys(12, "B", 42.0, _body(12, 1, 2_000_000))
    c = _sys(20, "Past B", 45.0, _body(20, 1, 1_500_000), y=1.0)
    done = Explored(mapped=frozenset({body_id64(11, 1)}))
    route = build_route([_result("riches", a, spent_s, b)], PREFS_OFF, done)
    pool = build_route([_result("riches", a, spent_s, b, c, n=30)], PREFS_OFF, done)
    out = swap_spent(route, pool, 25.0)
    assert [s.name for s in out.stops] == ["A", "B", "Past B"]

    def trip(r: Any) -> float:
        pts = [r.from_pos, *(s.pos for s in r.stops), r.to_pos]
        return sum(math.dist(p, q) for p, q in pairwise(pts))

    assert out.stops[2].detour_ly == pytest.approx(max(0.0, trip(out) - trip(route)))


def test_swapping_off_asks_spansh_once(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    _map_all(p, "Eol Prou BZ-A d138", 2)
    p.api_post("/api/scout", {**PREFS, "bio": False, "max_detour_ly": 0})
    _wait(p, "done")
    assert [dict(r.form or ())["max_results"] for r in t.requests if r.method == "POST"] == ["10"]
    stops = json.loads(p.snapshot)["state"]["scout"]["route"]["stops"]
    assert next(s for s in stops if s["name"] == "Eol Prou BZ-A d138")["spent"]
