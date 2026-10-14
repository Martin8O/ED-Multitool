""" "Worth it here?" for the system you arrive in (Spansh body search + your own FSS / DSS scans)."""

import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from edmultitool.catalog.genera import genus_name
from edmultitool.core.arrival import (
    Arrival,
    arrive,
    planet_values,
    scanned_body,
    star_value,
    survey,
    ui_arrival,
)
from edmultitool.core.events import (
    AllBodiesFound,
    BodyScanned,
    BodySignals,
    SystemSurveyed,
    parse_survey,
)
from edmultitool.core.explored import Explored, body_id64, species_key
from edmultitool.core.settings import ScoutPrefs, load_settings
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources import spansh
from edmultitool.sources.http import Request, Response
from fakes import FakeTime, ScriptedTransport, make_client, recorded, reply

pytestmark = pytest.mark.arrival

BOELTS = 180665307059
QAWAKANA = 4786481006963
PLAA = 642232231025  # Plaa Eurk QM-C b13-0
UNKNOWN = 1234567
# the route-scout form's defaults: mapping ≥ 1 M, bio ≥ 1 M per body, any planet, 50 000 ls
PREFS = ScoutPrefs()


def _done(id64: int, system: str, name: str) -> Arrival:
    t = ScriptedTransport(reply(name))
    found = spansh.system_bodies(make_client(t, FakeTime()), id64, threading.Event())
    return replace(arrive(id64, system), status="done", known=found)


# --- the value estimate ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["bodies_boelts", "bodies_qawakana", "bodies_plaa_eurk"])
def test_estimate_matches_spansh_on_every_recorded_planet(name: str) -> None:
    rows = recorded(name)["body"]["results"]
    planets = [r for r in rows if r["type"] == "Planet"]
    assert planets
    for r in planets:
        terra = r.get("terraforming_state") in spansh._TERRAFORMABLE
        assert planet_values(r["subtype"], r["earth_masses"], terra) == (
            r["estimated_scan_value"],
            r["estimated_mapping_value"],
        ), r["name"]


def test_star_estimate_matches_spansh() -> None:
    rows = recorded("bodies_boelts")["body"]["results"]
    stars = [r for r in rows if r["type"] == "Star" and r["subtype"][0] in "KMGF"]
    assert stars
    for r in stars:
        assert star_value(r["subtype"][0], r["solar_masses"]) == r["estimated_scan_value"], r["name"]


def test_your_scan_is_valued_like_spansh_values_it() -> None:
    elw = BodyScanned(1, 3, "X 3", "", "Earthlike body", 0.686172, False, False, 812.4, False, False)
    hmc = replace(elw, body_id=4, planet_class="High metal content body", mass=0.926552, terraformable=True)
    a, b = scanned_body(elw), scanned_body(hmc)
    assert (a.subtype, a.scan_value, a.mapping_value) == ("Earth-like world", 276191, 1003493)
    assert (b.subtype, b.scan_value, b.mapping_value, b.terraformable) == (
        "High metal content world", 171823, 624289, True,
    )  # fmt: skip
    tiny = scanned_body(replace(elw, planet_class="Icy body", mass=0.0003))
    assert (tiny.scan_value, tiny.mapping_value) == (500, 2221)


# --- the journal ----------------------------------------------------------------------------------------


def test_survey_entries_are_read() -> None:
    honk = {"event": "FSSDiscoveryScan", "BodyCount": 21, "SystemAddress": PLAA, "Progress": 0.3}
    assert parse_survey(honk) == SystemSurveyed(PLAA, 21)
    assert parse_survey({"event": "FSSAllBodiesFound", "SystemAddress": PLAA, "Count": 21}) == AllBodiesFound(
        PLAA
    )
    dss = {
        "event": "SAASignalsFound", "BodyName": "B 6", "SystemAddress": PLAA, "BodyID": 25,
        "Signals": [
            {"Type": "$SAA_SignalType_Biological;", "Count": 6},
            {"Type": "$PlanetaryMiningLocation_Name;", "Count": 12},
        ],
        "Genuses": [{"Genus": "$Codex_Ent_Bacterial_Genus_Name;", "Genus_Localised": "Bacterium"},
                    {"Genus": "$Codex_Ent_Shrubs_Genus_Name;"}],
    }  # fmt: skip
    signals = parse_survey(dss)
    assert signals == BodySignals(PLAA, 25, "B 6", 6, ("Bacterium", "$Codex_Ent_Shrubs_Genus_Name;"))
    star = {
        "event": "Scan", "ScanType": "AutoScan", "BodyName": "S", "BodyID": 0, "SystemAddress": PLAA,
        "DistanceFromArrivalLS": 0.0, "StarType": "K", "StellarMass": 0.78, "WasDiscovered": False,
    }  # fmt: skip
    s = parse_survey(star)
    assert isinstance(s, BodyScanned) and (s.star_type, s.mass, s.discovered, s.mapped) == (
        "K",
        0.78,
        False,
        None,
    )
    belt = {"event": "Scan", "BodyName": "S A Belt Cluster 1", "BodyID": 5, "SystemAddress": PLAA}
    assert parse_survey(belt) is None
    assert parse_survey({"event": "Scan", "BodyID": 1}) is None  # no system
    assert genus_name("$Codex_Ent_Shrubs_Genus_Name;") == "Frutexa" and genus_name("Tussocks") == "Tussock"
    assert genus_name("Mystery") == "Mystery"


def test_scans_of_another_system_change_nothing() -> None:
    a = arrive(PLAA, "Plaa")
    assert survey(a, SystemSurveyed(BOELTS, 9)) is a


# --- the card -------------------------------------------------------------------------------------------


def test_rich_system_counts_mapping_over_the_minimum() -> None:
    a = _done(BOELTS, "Boelts KT-Q d5-5", "bodies_boelts")
    ui = ui_arrival(a, replace(PREFS, min_mapping=300_000, planets="prime"), Explored())
    shown = {s.short: s.mapping_value for s in ui.spots if s.mapping_value}
    # every prime planet within 50 000 ls, values exactly as Spansh gives them
    assert shown == {
        "A 8": 1070081, "B 3": 1003493, "A 5": 696898, "A 7": 675036, "B 1": 652651, "A 6": 635531,
        "B 2": 624289, "A 7 a": 480159,
    }  # fmt: skip
    assert (ui.map_count, ui.map_value, ui.worth) == (8, 5838138, 5838138)
    assert ui.text == "5.8M Cr: map 8 · life on 1 unsampled"
    # life on C 1 b lies 370 000 ls out: past the distance limit, not listed
    assert [s.short for s in ui.spots if s.unknown] == ["A 10 a"]
    # the default 1 M mapping minimum keeps only the two Earth-likes
    ui = ui_arrival(a, PREFS, Explored())
    assert [s.short for s in ui.spots if s.mapping_value] == ["A 8", "B 3"]


def test_bodies_you_mapped_are_left_out() -> None:
    a = _done(BOELTS, "Boelts KT-Q d5-5", "bodies_boelts")
    a8 = next(b for b in a.known or () if b.name.endswith(" A 8"))
    ui = ui_arrival(a, PREFS, Explored(mapped=frozenset({body_id64(BOELTS, a8.body_id)})))
    assert [s.short for s in ui.spots if s.mapping_value] == ["B 3"] and ui.yours == 1
    b3 = next(b for b in a.known or () if b.name.endswith(" B 3"))
    both = frozenset({body_id64(BOELTS, a8.body_id), body_id64(BOELTS, b3.body_id)})
    ui = ui_arrival(a, replace(PREFS, max_distance_ls=0), Explored(mapped=both))
    assert ui.worth == 0 and ui.yours == 2


def test_species_you_analysed_are_left_out() -> None:
    a = _done(PLAA, "Plaa Eurk QM-C b13-0", "bodies_plaa_eurk")
    b6 = body_id64(PLAA, 25)
    done = Explored(
        analysed=frozenset({(b6, species_key("Stratum Tectonicas")), (b6, species_key("Tussock Cultro"))})
    )
    ui = ui_arrival(a, PREFS, done)
    spot = {s.short: s for s in ui.spots}
    # B 6: 28 222 100 known, less the two species analysed (checked by hand against Spansh)
    assert spot["B 6"].bio_value == 28_222_100 - 19_010_800 - 1_766_600
    assert spot["B 6 a"].bio_value == 14_813_500
    assert [n for n, _v in spot["B 6 a"].species][:2] == [
        "Stratum Laminamus",
        "Tubus Rosarium",
    ]  # dearest first
    assert spot["B 7"].unknown == 4 and spot["B 7"].bio_value == 0
    assert ui.text == "22.3M Cr: bio 2 · life on 1 unsampled"
    # without the analysed species B 6 is worth the whole 28.2 M
    assert {s.short: s.bio_value for s in ui_arrival(a, PREFS, Explored()).spots}["B 6"] == 28_222_100


def test_species_minimums_apply() -> None:
    a = _done(PLAA, "Plaa Eurk QM-C b13-0", "bodies_plaa_eurk")
    ui = ui_arrival(a, replace(PREFS, min_species=2_000_000, min_bio_body=20_000_000), Explored())
    spot = {s.short: s for s in ui.spots}
    # B 6 keeps Tectonicas + Lapis (21.5 M); B 6 a's 2 M+ species add up to 7.9 M < 20 M: not counted
    assert spot["B 6"].bio_value == 19_010_800 + 2_483_600
    assert "B 6 a" not in spot or spot["B 6 a"].bio_value == 0


def test_unknown_system_then_your_scans() -> None:
    a = _done(UNKNOWN, "Nowhere AA-A a0", "bodies_unknown")
    ui = ui_arrival(a, PREFS, Explored())
    assert ui.unknown_system and ui.known == 0 and ui.text == "unknown to Spansh: first discovery possible"
    elw = BodyScanned(
        UNKNOWN, 1, "Nowhere AA-A a0 1", "", "Earthlike body", 0.686172, False, False, 812.4, False, False
    )
    star = BodyScanned(UNKNOWN, 0, "Nowhere AA-A a0", "K", "", 0.78, False, False, 0.0, False, False)
    a = survey(survey(survey(a, SystemSurveyed(UNKNOWN, 2)), star), elw)
    a = survey(survey(a, BodySignals(UNKNOWN, 1, "", 3, ())), AllBodiesFound(UNKNOWN))
    ui = ui_arrival(a, PREFS, Explored())
    (spot,) = ui.spots
    assert (spot.short, spot.mapping_value, spot.new, spot.first, spot.unknown) == (
        "1",
        1003493,
        True,
        True,
        3,
    )
    assert (ui.new, ui.first, ui.body_count, ui.all_found) == (2, 2, 2, True)
    assert ui.text == "1.0M Cr: map 1 · life on 1 unsampled · 2 undiscovered"


def test_quiet_system() -> None:
    a = _done(PLAA, "Plaa Eurk QM-C b13-0", "bodies_plaa_eurk")
    ui = ui_arrival(a, replace(PREFS, min_species=50_000_000), Explored())
    assert ui.worth == 0 and ui.text == "life on 1 unsampled"
    ui = ui_arrival(replace(a, known=tuple(b for b in a.known or () if not b.bio_signals)), PREFS, Explored())
    assert ui.spots == () and ui.text == "nothing over your minimums"


def test_spansh_known_body_takes_your_discovery_mark_and_signals() -> None:
    a = _done(PLAA, "Plaa Eurk QM-C b13-0", "bodies_plaa_eurk")
    scan = BodyScanned(
        PLAA,
        27,
        "Plaa Eurk QM-C b13-0 B 7",
        "",
        "High metal content body",
        0.02,
        False,
        True,
        1629.7,
        False,
        None,
    )
    a = survey(survey(a, scan), BodySignals(PLAA, 27, "", 4, ("$Codex_Ent_Bacterial_Genus_Name;",)))
    spot = next(s for s in ui_arrival(a, PREFS, Explored()).spots if s.short == "B 7")
    assert (spot.first, spot.new, spot.genera) == (True, False, ("Bacterium",))


def test_your_scans_count_while_spansh_is_asked_or_down() -> None:
    elw = BodyScanned(
        BOELTS, 9, "Boelts KT-Q d5-5 A 8", "", "Earthlike body", 1.656742, False, False, 2271.1, True, True
    )
    a = survey(arrive(BOELTS, "Boelts KT-Q d5-5"), elw)
    ui = ui_arrival(a, PREFS, Explored())
    assert ui.status == "looking" and ui.worth == 1070081 and ui.new == 0
    assert ui.text == "looking up… · 1.1M Cr: map 1"
    failed = ui_arrival(replace(a, status="error", message="Spansh: down."), PREFS, Explored())
    assert failed.text == "no answer from Spansh · 1.1M Cr: map 1" and not failed.unknown_system
    assert ui_arrival(arrive(BOELTS, "B"), PREFS, Explored()).text == "looking up…"
    assert ui_arrival(Arrival(), PREFS, Explored()).text == "—"


# --- Spansh ---------------------------------------------------------------------------------------------


def test_body_search_request_and_cache(tmp_path: Path) -> None:
    t = ScriptedTransport(reply("bodies_plaa_eurk"))
    client = make_client(t, FakeTime(), cache_dir=tmp_path)
    found = spansh.system_bodies(client, PLAA, threading.Event())
    assert len(found) == 21
    (req,) = t.requests
    assert req.url == "https://spansh.co.uk/api/bodies/search"
    assert json.loads(req.json_body or "") == {
        "filters": {"system_id64": {"value": PLAA}},
        "size": 100,
        "page": 0,
    }
    assert spansh.system_bodies(client, PLAA, threading.Event()) == found  # the cached copy: no 2nd call
    b6 = next(b for b in found if b.body_id == 25)
    assert (b6.bio_signals, b6.genera[:2], len(b6.species)) == (6, ("Cactoida", "Bacterium"), 6)


def test_body_search_pages_through_a_big_system() -> None:
    rows = recorded("bodies_boelts")["body"]["results"]

    def page(req: Request) -> Response:
        n = json.loads(req.json_body or "")["page"]
        part = rows[n * 20 : n * 20 + 20] if n < 2 else []
        return Response(200, json.dumps({"count": 39, "results": part}).encode())

    t = ScriptedTransport(page, repeat_last=True)
    old = spansh.BODIES_PAGE
    spansh.BODIES_PAGE = 20
    try:
        found = spansh.system_bodies(make_client(t, FakeTime()), BOELTS, threading.Event())
    finally:
        spansh.BODIES_PAGE = old
    assert len(found) == 39 and len(t.requests) == 2


# --- end to end through the plugin ----------------------------------------------------------------------


def _answer(req: Request) -> Response:
    if req.url.endswith("/bodies/search"):
        system = json.loads(req.json_body or "")["filters"]["system_id64"]["value"]
        return reply({PLAA: "bodies_plaa_eurk", BOELTS: "bodies_boelts"}.get(system, "bodies_unknown"))
    raise AssertionError(f"unexpected call {req.url}")


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path)
    t = ScriptedTransport(_answer, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()


def _pump_until(p: ScoutPlugin, done: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert done(), p.arrival


def _jump(system: str, address: int) -> dict[str, Any]:
    return {"event": "FSDJump", "StarSystem": system, "SystemAddress": address, "StarPos": [0, 0, 0]}


def test_arrival_end_to_end(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    p.on_journal(_jump("Plaa Eurk QM-C b13-0", PLAA), None)
    assert p.ui.panel.here == "looking up…"
    _pump_until(p, lambda: p.arrival.status == "done")
    state = json.loads(p.snapshot)["state"]
    assert state["arrival"]["system"] == "Plaa Eurk QM-C b13-0" and state["arrival"]["known"] == 21
    assert state["panel"]["here"] == p.ui.arrival.text and p.ui.arrival.bio_count == 2
    dump = json.loads(p.debug_path.read_text(encoding="utf-8"))
    assert dump["panel"]["here"] == p.ui.panel.here and dump["arrival"]["known"] == 21
    # a scan here adds to the card; mapping it with probes takes its value off
    p.on_journal({"event": "FSSDiscoveryScan", "BodyCount": 21, "SystemAddress": PLAA}, None)
    assert p.ui.arrival.body_count == 21
    p.on_journal(_jump("Boelts KT-Q d5-5", BOELTS), None)
    _pump_until(p, lambda: p.arrival.status == "done" and p.arrival.address == BOELTS)
    assert p.ui.arrival.body_count is None and [s.short for s in p.ui.arrival.spots if s.mapping_value] == [
        "A 8",
        "B 3",
    ]
    a8 = next(b for b in p.arrival.known or () if b.name.endswith(" A 8"))
    p.on_journal(
        {
            "event": "SAAScanComplete",
            "SystemAddress": BOELTS,
            "BodyID": a8.body_id,
            "timestamp": "2026-10-13T18:00:00Z",
        },
        None,
    )
    assert [s.short for s in p.ui.arrival.spots if s.mapping_value] == ["B 3"]
    assert len(t.requests) == 2


def test_arrival_failure_is_shown(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    t.steps = [Response(503, b"down")]
    p.on_journal(_jump("Plaa Eurk QM-C b13-0", PLAA), None)
    _pump_until(p, lambda: p.arrival.status == "error", timeout=30)
    assert p.arrival.message.startswith("Spansh: ") and p.ui.panel.here == "no answer from Spansh"


def test_arrival_before_the_worker_is_asked_when_it_starts(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.on_journal(_jump("Plaa Eurk QM-C b13-0", PLAA), None)
    p.on_journal({"event": "FSSDiscoveryScan", "BodyCount": 21, "SystemAddress": PLAA}, None)
    assert p.arrival.status == "looking"
    t = ScriptedTransport(_answer, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    try:
        _pump_until(p, lambda: p.arrival.status == "done")
        assert p.arrival.body_count == 21  # the scan seen before the worker ran is kept
    finally:
        p.stop_worker()


def test_crew_in_another_ship_clears_the_card(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.on_journal(_jump("Plaa Eurk QM-C b13-0", PLAA), None)
    _pump_until(p, lambda: p.arrival.status == "done")
    p.on_journal({"event": "JoinACrew", "Captain": "x"}, None)
    assert p.arrival.status == "none" and p.ui.panel.here == "—"
    p.on_journal({**_jump("Boelts KT-Q d5-5", BOELTS), "Multicrew": True}, None)  # the captain's jump
    assert p.arrival.status == "none"


def test_a_spansh_row_without_values_takes_your_estimate() -> None:
    a = _done(BOELTS, "Boelts KT-Q d5-5", "bodies_boelts")
    a8 = next(b for b in a.known or () if b.name.endswith(" A 8"))
    a = replace(
        a, known=tuple(replace(b, scan_value=0, mapping_value=0) if b is a8 else b for b in a.known or ())
    )
    assert "A 8" not in {s.short for s in ui_arrival(a, PREFS, Explored()).spots}
    scan = BodyScanned(
        BOELTS, a8.body_id, a8.name, "", "Earthlike body", 1.656742, False, False, 2271.1, True, True
    )
    spot = next(s for s in ui_arrival(survey(a, scan), PREFS, Explored()).spots if s.short == "A 8")
    assert spot.mapping_value == 1070081 and not spot.new


def test_a_reply_for_a_system_you_left_is_dropped(tmp_path: Path) -> None:
    first_asked, second_jump = threading.Event(), threading.Event()

    def slow_then_fast(req: Request) -> Response:
        system = json.loads(req.json_body or "")["filters"]["system_id64"]["value"]
        if system == PLAA:  # held until you have jumped on
            first_asked.set()
            second_jump.wait(5)
        return _answer(req)

    p = ScoutPlugin(tmp_path)
    p.client = make_client(ScriptedTransport(slow_then_fast, repeat_last=True), FakeTime())
    p.start_worker()
    try:
        p.on_journal(_jump("Plaa Eurk QM-C b13-0", PLAA), None)
        assert first_asked.wait(5)
        p.on_journal(_jump("Boelts KT-Q d5-5", BOELTS), None)
        second_jump.set()
        _pump_until(p, lambda: p.arrival.status == "done")
        assert p.arrival.address == BOELTS and len(p.arrival.known or ()) == 39
        p.pump()
        assert p.ui.arrival.system == "Boelts KT-Q d5-5" and p.ui.arrival.known == 39
    finally:
        p.stop_worker()


# --- flashing the window over a rich body ---------------------------------------------------------------


def test_best_body_is_reported_for_the_flash() -> None:
    a = _done(PLAA, "Plaa Eurk QM-C b13-0", "bodies_plaa_eurk")
    assert ui_arrival(a, PREFS, Explored()).best == 28_222_100  # B 6, the dearest single body


def test_flash_setting_is_checked_saved_and_shown(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    assert (p.ui.flash, p.ui.flash_over) == (True, 30_000_000)
    for bad in (
        {"on": True},
        {"on": "yes", "over": 30e6},
        {"on": True, "over": 1},
        {"on": True, "over": 2e9},
    ):
        answer = p.api_post("/api/flash", bad)
        assert answer is not None and answer[0] == 400
    assert p.api_post("/api/flash", {"on": False, "over": 45_000_000}) == (202, {"accepted": "flash"})
    p.pump()
    state = json.loads(p.snapshot)["state"]
    assert (state["flash"], state["flash_over"]) == (False, 45_000_000)
    saved = json.loads(p.settings_path.read_text(encoding="utf-8"))
    assert (saved["flash"], saved["flash_over"]) == (False, 45_000_000)
    assert load_settings(p.settings_path).flash_over == 45_000_000
    p.settings_path.write_text(json.dumps({"flash": "x", "flash_over": 5}), encoding="utf-8")
    assert (load_settings(p.settings_path).flash, load_settings(p.settings_path).flash_over) == (
        True,
        30_000_000,
    )
