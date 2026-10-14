"""The nearest places your ship can land with the services asked for, from Spansh's station search
(recorded)."""

import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from edmultitool.core.market import Standings
from edmultitool.core.nearby import (
    ServiceQuery,
    ServiceRequest,
    split_barred,
    ui_service_rows,
    usable,
)
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources import spansh
from edmultitool.sources.http import Response
from edmultitool.station_lookup import find
from fakes import FakeTime, ScriptedTransport, make_client, recorded, reply

DATA = Path(__file__).parent / "data"
SOL = (0.0, 0.0, 0.0)
SOL_ID = 10477373803


def _query(**kw: object) -> ServiceQuery:
    return ServiceQuery(ServiceRequest(**kw), "Sol", SOL_ID, SOL)  # type: ignore[arg-type]


def test_the_form_is_the_recorded_one() -> None:
    q = _query(pad="L", settlements=False)
    assert spansh.stations_form(q, 0) == recorded("stations_sol_large")["request"]["json"]
    form = spansh.stations_form(_query(pad="M", carriers=True), 1)
    types = form["filters"]["type"]["value"]
    assert "Settlement" in types and "Drake-Class Carrier" in types and "has_large_pad" not in form["filters"]
    assert form["page"] == 1 and form["reference_coords"] == {"x": 0.0, "y": 0.0, "z": 0.0}


def test_services_asked_must_all_be_there() -> None:
    wanted = ("Black Market", "Interstellar Factors Contact")
    q = _query(pad="L", settlements=False, services=wanted)
    assert spansh.stations_form(q, 0) == recorded("stations_sol_services")["request"]["json"]
    assert spansh.stations_form(q, 0)["filters"]["services"] == [{"name": s} for s in wanted]  # every one
    assert "services" not in spansh.stations_form(_query(pad="L"), 0)["filters"]  # none ticked: any station
    client = make_client(ScriptedTransport(reply("stations_sol_services")), FakeTime())
    rows, _ = spansh.stations_page(client, q, 0, threading.Event())
    assert rows[0].station == "Hair Obligation" and all(r.pad == "L" for r in rows)


def test_rows_are_parsed_with_faction_and_pad() -> None:
    client = make_client(ScriptedTransport(reply("stations_sol_medium")), FakeTime())
    rows, total = spansh.stations_page(client, _query(pad="M"), 0, threading.Event())
    assert total == 10000 and len(rows) == 25
    ehrlich = rows[0]
    assert (ehrlich.station, ehrlich.station_type, ehrlich.pad, ehrlich.faction) == (
        "Ehrlich City", "Planetary Port", "L", "Sol Workers' Party",
    )  # fmt: skip
    burnell = next(r for r in rows if r.station == "Burnell Station")
    assert burnell.pad == "M" and not burnell.carrier
    megaship = next(r for r in rows if r.station_type == "Mega ship")
    assert megaship.pad == ""  # no pads known: never a place to land


def test_in_one_system_the_nearest_to_the_star_comes_first() -> None:
    client = make_client(ScriptedTransport(reply("stations_sol_medium")), FakeTime())
    q = _query(pad="M")
    rows, _ = spansh.stations_page(client, q, 0, threading.Event())
    got = usable(rows, q)
    assert [r.station for r in got[:5]] == [
        "Ehrlich City", "Furukawa Enterprise", "Burnell Station", "Galileo", "Mars High",
    ]  # fmt: skip
    assert "Rescue Ship - Li Qing Jao" not in {r.station for r in got}
    large = usable(rows, _query(pad="L"))
    assert "Burnell Station" not in {r.station for r in large}  # medium pads only


def test_a_page_ending_in_the_same_system_asks_for_the_next() -> None:
    t = ScriptedTransport(reply("stations_sol_medium"), repeat_last=True)
    client = make_client(t, FakeTime())
    got = find(client, _query(pad="M"), Standings(), threading.Event())
    assert len(t.requests) == 4  # every row of the page was in Sol: up to MAX_PAGES asked
    assert len({(r.system, r.station) for r in got}) == len(got)  # each station once


def test_hostile_factions_are_hidden_only_when_asked() -> None:
    client = make_client(ScriptedTransport(reply("stations_sol_medium")), FakeTime())
    q = _query(pad="M")
    rows = usable(spansh.stations_page(client, q, 0, threading.Event())[0], q)
    you = Standings({"sol workers' party": -100.0})
    kept, barred = split_barred(rows, q.request, you)
    assert barred and all(r.faction == "Sol Workers' Party" for r in barred)
    assert all(r.faction != "Sol Workers' Party" for r in kept)
    shown = ui_service_rows((), rows[:3], you)
    assert (shown[0].standing, shown[0].barred) == ("hostile", True)
    open_q = _query(pad="M", hide_barred=False)
    assert split_barred(rows, open_q.request, you) == (rows, [])


# --- the plugin end to end --------------------------------------------------------------------------------


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path)
    for line in (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            p.on_journal(json.loads(line), None)
    p.on_journal(
        {"event": "FSDJump", "StarSystem": "Sol", "SystemAddress": SOL_ID, "StarPos": [0, 0, 0]}, None
    )
    t = ScriptedTransport(reply("stations_sol_medium"), repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()


def _pump_until(p: ScoutPlugin, done: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert done(), p.services


def test_any_station_end_to_end(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    p.factions.records.clear()
    p.factions.record("Sol Workers' Party").reputation = -100.0  # hostile: its stations are hidden
    assert p.api_post("/api/services", {"pad": "M"}) == (
        202,
        {"accepted": "services"},
    )
    _pump_until(p, lambda: p.services.status == "done")
    v = p.ui.services
    assert v.source == "Spansh" and v.asked == () and len(v.rows) == 10
    assert v.rows[0].station == "Galileo" and v.rows[0].distance_ly == 0.0
    assert all(r.faction != "Sol Workers' Party" for r in v.rows)
    assert v.barred > 0 and v.message.startswith("The 10 nearest places with a medium pad")
    assert "would refuse you docking" in v.message
    assert t.requests[0].url.endswith("/stations/search")


def test_any_station_with_services_says_which(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    t.steps = [reply("stations_sol_services")]
    services = ["Black Market", "Interstellar Factors Contact"]
    p.api_post("/api/services", {"pad": "L", "settlements": False, "services": services})
    _pump_until(p, lambda: p.services.status == "done")
    v = p.ui.services
    assert v.rows[0].station == "Hair Obligation"
    assert v.message.startswith(
        "The 10 nearest places with a large pad, Black Market and Interstellar Factors,"
    )
    assert t.requests[0].json_body is not None and '"services"' in t.requests[0].json_body
    assert v.asked == tuple(services) and all(r.kind == "" for r in v.rows)  # no trader asked: no kind
    shown = json.loads(p.snapshot)["state"]["services"]  # what the page ticks again after a reload
    assert (shown["asked"], shown["pad"], shown["settlements"], shown["carriers"], shown["hide_barred"]) == (
        services, "L", False, False, True,
    )  # fmt: skip


def test_a_trader_of_one_kind_end_to_end(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    t.steps = [reply("stations_sol_trader_raw")]
    p.api_post("/api/services", {"pad": "M", "services": ["Material Trader"], "trader_kind": "Raw"})
    _pump_until(p, lambda: p.services.status == "done")
    v = p.ui.services
    assert v.message.startswith(
        "The 10 nearest places with a medium pad, Material Trader (Raw), nearest first."
    )
    assert v.rows[0].station == "Broglie Terminal" and {r.kind for r in v.rows} == {"Raw"}
    sent = json.loads(t.requests[0].json_body or "{}")["filters"]
    assert sent["primary_economy"] == {"value": ["Extraction", "Refinery"]}


def test_kinds_no_station_can_have_are_answered_at_once(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, t = plugin
    body = {
        "pad": "M",
        "services": ["Material Trader", "Technology Broker"],
        "trader_kind": "Raw",
        "broker_kind": "Human",
    }
    p.api_post("/api/services", body)
    _pump_until(p, lambda: p.services.status == "done")
    assert p.services.message.startswith(
        "No station can have a medium pad, Material Trader (Raw) and Technology"
    )
    assert p.services.rows == () and not t.requests  # nothing asked online


def test_any_station_failure_names_spansh(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    t.steps = [Response(503, b"{}")]
    p.api_post("/api/services", {"pad": "L"})
    _pump_until(p, lambda: p.services.status == "error")
    assert p.services.message.startswith("Spansh: ")
