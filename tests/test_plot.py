"""Route-only plots: Spansh's galaxy plotter on trimmed real replies (tests/data/spansh/, recorded by
`tools/smoke_spansh.py --plots --record`), normal jumps and with neutron boosts; the ship build it needs, the
page's view of a plot, and the plugin flow from the form to the published plot."""

import json
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from edmultitool.catalog.fsd import supercharge_multiplier
from edmultitool.core.events import parse_event
from edmultitool.core.plot import PlotQuery, PlotResult, ShipBuild, ship_build
from edmultitool.core.settings import ScoutPrefs, normalise, parse_scout_prefs
from edmultitool.core.view import ui_plot
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources import spansh
from edmultitool.sources.http import Request, Response, SourceError
from fakes import FakeTime, ScriptedTransport, make_client, recorded, reply
from test_scout_api import PREFS, _pump_until, _sample

SOL = 10477373803
MAIA = 8216113749
KINDS = ("exact", "neutron")


def _query(kind: str) -> PlotQuery:
    """The query a recording was made with, read back from its form."""
    f = dict(recorded(f"{kind}_submit")["request"]["form"])
    ship = ShipBuild(
        optimal_mass=float(f["optimal_mass"]),
        max_fuel_per_jump=float(f["max_fuel_per_jump"]),
        fuel_multiplier=float(f["fuel_multiplier"]),
        fuel_power=float(f["fuel_power"]),
        base_mass=float(f["base_mass"]),
        tank_size=float(f["tank_size"]),
        reservoir_size=float(f["internal_tank_size"]),
        range_boost=float(f["range_boost"]),
        supercharge_multiplier=int(f["supercharge_multiplier"]),
    )
    return PlotQuery(
        int(f["source"]), int(f["destination"]), ship, int(f["cargo"]), f["use_supercharge"] == "1"
    )


def _plot(
    query: PlotQuery, *steps: Any, cache_dir: Path | None = None, fake: FakeTime | None = None
) -> tuple[PlotResult, ScriptedTransport]:
    t = ScriptedTransport(*steps)
    return spansh.plot(make_client(t, fake or FakeTime(), cache_dir=cache_dir), query, threading.Event()), t


def _recorded_plot(kind: str) -> PlotResult:
    return _plot(_query(kind), reply(f"{kind}_submit"), reply(f"{kind}_done"))[0]


# --- request shape --------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", KINDS)
def test_request_shape_matches_what_spansh_accepted(kind: str) -> None:
    rec = recorded(f"{kind}_submit")["request"]
    assert spansh.plot_form(_query(kind)) == [tuple(p) for p in rec["form"]]
    assert rec["url"] == f"{spansh.BASE}/generic/route"


def test_form_asks_for_neutrons_only_when_wanted_and_needed_refuels_only() -> None:
    # each probed live at both values on 2026-10-12 (docs/api-notes.md)
    normal, boosted = (dict(spansh.plot_form(_query(k))) for k in KINDS)
    assert (normal["use_supercharge"], boosted["use_supercharge"]) == ("0", "1")
    assert normal["refuel_every_scoopable"] == boosted["refuel_every_scoopable"] == "0"
    assert normal["base_mass"] == "573.549988"  # exact decimals, never an exponent or a rounded figure
    assert dict(spansh.plot_form(replace(_query("exact"), cargo=100)))["cargo"] == "100"


# --- parsing --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", KINDS)
def test_plot_is_one_row_per_jump(kind: str) -> None:
    result, t = _plot(_query(kind), reply(f"{kind}_submit"), reply(f"{kind}_done"))
    raw = recorded(f"{kind}_done")["body"]["result"]["jumps"]
    job = recorded(f"{kind}_submit")["body"]["job"]
    assert [r.method for r in t.requests] == ["POST", "GET"]
    assert t.requests[1].url == f"{spansh.BASE}/results/{job}"
    w = result.waypoints
    assert result.kind == kind and not result.from_cache
    assert [x.id64 for x in w] == [int(r["id64"]) for r in raw]
    assert (w[0].id64, w[-1].id64) == (SOL, MAIA)
    assert [x.leg_ly for x in w] == [r["distance"] for r in raw]
    assert [(x.refuel, x.neutron) for x in w] == [(r["must_refuel"], r["has_neutron"]) for r in raw]
    assert [x.scoopable for x in w[1:-1]] == [r["is_scoopable"] for r in raw[1:-1]]
    # Spansh says "not scoopable" for both ends whatever the star (Maia is a B star): not known
    assert not raw[0]["is_scoopable"] and not raw[-1]["is_scoopable"]
    assert w[0].scoopable is None and w[-1].scoopable is None
    assert w[0].jumps == 0 and all(x.jumps == 1 for x in w[1:])
    assert result.total_jumps == len(raw) - 1
    assert all(x.scoopable for x in w if x.refuel)


def test_normal_and_neutron_routes_differ_as_spansh_plotted_them() -> None:
    normal, boosted = (_recorded_plot(k) for k in KINDS)
    assert (normal.total_jumps, boosted.total_jumps) == (20, 18)
    assert not any(x.neutron for x in normal.waypoints)
    # the neutron mark sits on the star to supercharge at; the jump after it is the long one
    (i,) = [i for i, x in enumerate(boosted.waypoints) if x.neutron]
    assert boosted.waypoints[i + 1].leg_ly > 4 * 22 > max(x.leg_ly for x in normal.waypoints)


def test_neutron_mark_is_never_read_on_a_normal_route() -> None:
    """Spansh's flag means "supercharge here" only when neutrons were asked for."""
    doc = recorded("neutron_done")["body"]  # a reply that does carry one neutron mark
    as_normal = Response(200, json.dumps(doc).encode())
    plot, _ = _plot(_query("exact"), reply("exact_submit"), as_normal)
    assert any(r["has_neutron"] for r in doc["result"]["jumps"])
    assert not any(x.neutron for x in plot.waypoints)


def test_plot_is_cached_by_query(tmp_path: Path) -> None:
    fake = FakeTime()
    q = _query("exact")
    first, _ = _plot(q, reply("exact_submit"), reply("exact_done"), cache_dir=tmp_path, fake=fake)
    again, t = _plot(q, cache_dir=tmp_path, fake=fake)  # any call would fail
    assert t.requests == [] and again.from_cache and again.waypoints == first.waypoints
    # more cargo, or neutrons, is another route
    for other_q in (replace(q, cargo=100), replace(q, neutrons=True)):
        _, other = _plot(other_q, reply("exact_submit"), reply("exact_done"), cache_dir=tmp_path, fake=fake)
        assert len(other.requests) == 2


def test_plot_without_rows_is_a_bad_reply() -> None:
    empty = Response(200, json.dumps({"status": "ok", "result": {"jumps": []}}).encode())
    with pytest.raises(SourceError, match="no route came back"):
        _plot(_query("exact"), reply("exact_submit"), empty)


def test_resolve_system_needs_the_exact_name() -> None:
    names = recorded("names")["body"]["min_max"]
    t = ScriptedTransport(reply("names"), repeat_last=True)
    client = make_client(t, FakeTime())
    hit = spansh.resolve_system(client, names[1]["name"].upper(), threading.Event())
    assert hit.id64 == names[1]["id64"]
    with pytest.raises(SourceError, match="does not know a system called 'Col 285 Sector'"):
        spansh.resolve_system(client, " Col 285 Sector ", threading.Event())


TWINS = [
    {"id64": 1, "name": "Twin", "x": 0, "y": 0, "z": 0},
    {"id64": 2, "name": "Twin", "x": 9, "y": 0, "z": 0},
]


def test_resolve_system_refuses_a_shared_name() -> None:
    client = make_client(
        ScriptedTransport(Response(200, json.dumps({"min_max": TWINS}).encode())), FakeTime()
    )
    with pytest.raises(SourceError, match="knows 2 systems called 'Twin'"):
        spansh.resolve_system(client, "Twin", threading.Event())


# --- ship build ------------------------------------------------------------------------------------------
def test_ship_build_from_the_journal_matches_spansh_own_loadout_import() -> None:
    loadout = next(e for e in _sample() if e.get("event") == "Loadout")
    ship = parse_event(loadout)
    assert ship is not None and not isinstance(ship, list)
    build = ship_build(ship)  # type: ignore[arg-type]
    assert build is not None
    fsd = next(m for m in loadout["Modules"] if m["Slot"] == "FrameShiftDrive")
    assert build.base_mass == pytest.approx(loadout["UnladenMass"] + loadout["FuelCapacity"]["Reserve"])
    assert build.tank_size == loadout["FuelCapacity"]["Main"]
    assert build.reservoir_size == loadout["FuelCapacity"]["Reserve"]
    assert build.supercharge_multiplier == supercharge_multiplier(fsd["Item"])


def test_supercharge_is_x6_only_for_the_sco_mk_ii() -> None:
    assert supercharge_multiplier("int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii") == 6
    assert supercharge_multiplier("Int_Hyperdrive_Overcharge_Size5_Class5") == 4
    assert supercharge_multiplier("int_hyperdrive_size5_class5") == 4


# --- the form -------------------------------------------------------------------------------------------
def test_route_only_form_and_plotter_choice() -> None:
    route_only = parse_scout_prefs({**PREFS, "riches": False, "bio": False, "plotter": "neutron"})
    assert isinstance(route_only, ScoutPrefs) and route_only.plotter == "neutron"
    bad = parse_scout_prefs({**PREFS, "plotter": "fast"})
    assert bad == "'plotter' must be one of: exact, neutron"
    assert normalise({"scout": {**PREFS, "plotter": "fast"}}).scout.plotter == "exact"
    assert normalise({"scout": {**PREFS, "plotter": "neutron"}}).scout.plotter == "neutron"


# --- the page's view -------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", KINDS)
def test_ui_plot_rows_totals_and_replan_basis(kind: str) -> None:
    plot = _recorded_plot(kind)
    ui = ui_plot(plot, 22.0929, "2026-10-12T00:00:00+00:00")
    assert (ui.mode, ui.range_basis, ui.range_ly, ui.stops) == (kind, "full", 22.09, ())
    assert (ui.from_name, ui.to_name, ui.total_jumps) == ("Sol", "Maia", plot.total_jumps)
    assert ui.total_ly == pytest.approx(383.31, abs=0.01)  # Sol → Maia straight
    assert [w.id64 for w in ui.waypoints] == [str(x.id64) for x in plot.waypoints]
    assert [(w.neutron, w.refuel, w.scoopable) for w in ui.waypoints] == [
        (x.neutron, x.refuel, x.scoopable) for x in plot.waypoints
    ]
    assert all(round(w.leg_ly, 2) == w.leg_ly for w in ui.waypoints)


# --- plugin: form → worker → published plot ----------------------------------------------------------------
def _plotter(request: Request) -> Response:
    """Answers like Spansh did for the recorded plots; name lookups are prefix matches in any letter case,
    as Spansh's autocomplete is (the smoke resolves "sol")."""
    if request.method == "GET" and "/field_values/system_names" in request.url:
        q = dict(request.params)["q"]
        rows: list[dict[str, Any]] = [
            {"id64": MAIA, "name": "Maia", "x": -81.78125, "y": -149.4375, "z": -343.375},
            {"id64": SOL, "name": "Sol", "x": 0, "y": 0, "z": 0},
        ]
        return Response(
            200,
            json.dumps({"min_max": [r for r in rows if r["name"].lower().startswith(q.lower())]}).encode(),
        )
    if request.method == "POST":
        _plotter.kind = "neutron" if dict(request.form or ())["use_supercharge"] == "1" else "exact"  # type: ignore[attr-defined]
        return reply(f"{_plotter.kind}_submit")  # type: ignore[attr-defined]
    return reply(f"{_plotter.kind}_done")  # type: ignore[attr-defined]


@pytest.fixture
def plugin(tmp_path: Path) -> Any:
    p = ScoutPlugin(tmp_path)
    for entry in _sample():
        p.on_journal(entry, None)
    t = ScriptedTransport(_plotter, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()


ROUTE_ONLY = {**PREFS, "riches": False, "bio": False}


@pytest.mark.parametrize("kind", KINDS)
def test_plot_from_the_current_system_uses_its_id_and_the_ship(plugin: Any, kind: str) -> None:
    p, t = plugin
    p.api_post("/api/scout", {**ROUTE_ONLY, "from_system": "", "to_system": "maia", "plotter": kind})
    _pump_until(p, "done")
    (post,) = [r for r in t.requests if r.method == "POST"]
    form = dict(post.form or ())
    assert post.url == f"{spansh.BASE}/generic/route"
    assert form["use_supercharge"] == ("1" if kind == "neutron" else "0")
    assert form["source"] == str(p.state.address)  # no name lookup for where we are
    assert form["destination"] == str(MAIA)
    assert form["cargo"] == str(p.state.cargo)
    build = ship_build(p.state.ship)
    assert build is not None and form["optimal_mass"] == spansh._dec(build.optimal_mass)
    looked_up = [dict(r.params)["q"] for r in t.requests if r.method == "GET" and "system_names" in r.url]
    assert looked_up == ["maia"]
    route = json.loads(p.snapshot)["state"]["scout"]["route"]
    assert route["mode"] == kind and route["range_basis"] == "full"
    assert route["range_ly"] == p.ui.ranges.full
    assert route["total_jumps"] == len(recorded(f"{kind}_done")["body"]["result"]["jumps"]) - 1
    assert all(isinstance(w["id64"], str) for w in route["waypoints"])
    dump = json.loads((p.data_dir / "debug" / "panel.json").read_text(encoding="utf-8"))
    assert dump["scout"]["mode"] == kind and dump["scout"]["waypoints"] == len(route["waypoints"])


def test_plot_looks_up_a_typed_start(plugin: Any) -> None:
    p, t = plugin
    p.api_post("/api/scout", {**ROUTE_ONLY, "from_system": "Sol", "to_system": "Maia"})
    _pump_until(p, "done")
    (post,) = [r for r in t.requests if r.method == "POST"]
    assert dict(post.form or ())["source"] == str(SOL)


def test_plot_unknown_destination_is_named(plugin: Any) -> None:
    p, t = plugin
    p.api_post("/api/scout", {**ROUTE_ONLY, "to_system": "Nowhere"})
    _pump_until(p, "error")
    assert (
        p.scout.message == "Spansh does not know a system called 'Nowhere'. Pick a name from the suggestions."
    )
    assert not [r for r in t.requests if r.method == "POST"]  # no plot job without both ends


def test_plot_shared_name_is_named(plugin: Any) -> None:
    p, t = plugin
    t.steps = [Response(200, json.dumps({"min_max": TWINS}).encode())]
    p.api_post("/api/scout", {**ROUTE_ONLY, "from_system": "", "to_system": "Twin"})
    _pump_until(p, "error")
    assert p.scout.message == (
        "Spansh knows 2 systems called 'Twin'; cannot tell which one. Pick a name from the suggestions."
    )


def test_cancel_during_the_name_lookup_ends_the_plot(plugin: Any) -> None:
    p, t = plugin
    gate = threading.Event()

    def slow(request: Request) -> Response:
        gate.wait(5)
        return _plotter(request)

    t.steps = [slow]
    p.api_post("/api/scout", {**ROUTE_ONLY, "from_system": "", "to_system": "Maia"})
    _pump_until(p, "running")
    p.api_post("/api/scout/cancel", {})
    _pump_until(p, "idle")
    gate.set()
    time.sleep(0.2)
    p.pump()
    assert p.scout.status == "idle" and p.scout.message == "Search cancelled." and p.scout.route is None
    assert not [r for r in t.requests if r.method == "POST"]  # the plot job was never submitted


def test_route_only_without_a_ship_is_told(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    for plotter in KINDS:
        p.api_post("/api/scout", {**ROUTE_ONLY, "plotter": plotter})
        p.pump()
        assert p.scout.status == "error" and p.scout.message.startswith("No jump range yet")


def test_route_only_to_the_start_itself_is_refused(plugin: Any) -> None:
    p, t = plugin
    for plotter in KINDS:
        p.api_post("/api/scout", {**ROUTE_ONLY, "to_system": "sol", "plotter": plotter})
        p.pump()
        assert p.scout.status == "error" and p.scout.message == "Start and destination are the same system."
    assert t.requests == []  # Spansh would answer with a 0 ly route (probed 2026-10-12)


def test_route_only_form_is_remembered(plugin: Any) -> None:
    p, _ = plugin
    form = {**ROUTE_ONLY, "to_system": "Maia", "plotter": "neutron"}
    p.api_post("/api/scout", form)
    _pump_until(p, "done")
    assert asdict(p.settings.scout) == form
