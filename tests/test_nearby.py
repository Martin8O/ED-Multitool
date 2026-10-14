"""Nearest engineer (bundled catalog) and the station request, kinds and rows (pure; recorded replies)."""

import json
import math
import threading
from pathlib import Path
from typing import Any

import pytest

from edmultitool.catalog.engineers import ENGINEERS, MODULES
from edmultitool.core.nearby import (
    ServiceQuery,
    ServiceRequest,
    ServiceRow,
    UiServices,
    broker_kind,
    parse_service_request,
    row_kind,
    trader_kind,
    ui_engineers,
    ui_service_rows,
    usable,
    what_asked,
    with_distances,
)
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources import spansh
from fakes import FakeTime, ScriptedTransport, make_client, reply

DATA = Path(__file__).parent / "data"
SOL = (0.0, 0.0, 0.0)
SOL_ID = 10477373803
TRADER = ("Material Trader",)


def _query(pad: Any = "L", **kw: Any) -> ServiceQuery:
    return ServiceQuery(ServiceRequest(pad, **kw), "Sol", SOL_ID, SOL)


def _row(name: str, x: float = 1.0, pad: str = "L", system: str = "") -> ServiceRow:
    return ServiceRow(
        name, "Coriolis Starport", system or f"S {name}", 1, x, 0, 0, 10.0, pad, "Industrial", ""
    )


# --- the engineer catalog ------------------------------------------------------------------------------
def test_catalog_has_every_engineer_once() -> None:
    assert len(ENGINEERS) == 38
    assert sum(e.ship for e in ENGINEERS) == 25
    assert len({e.name for e in ENGINEERS}) == 38
    assert len({e.system_id64 for e in ENGINEERS}) == 38
    for e in ENGINEERS:
        assert e.system and e.base, e.name
        assert bool(e.modules) == e.ship, e.name  # what on-foot engineers improve is not bundled
        assert set(e.modules) <= set(MODULES), e.name
        assert all(1 <= g <= 5 for g in e.modules.values()), e.name


def test_catalog_spot_checks() -> None:
    by = {e.name: e for e in ENGINEERS}
    farseer = by["Felicity Farseer"]
    assert (farseer.system, farseer.base, farseer.system_id64) == ("Deciat", "Farseer Inc", 6681123623626)
    assert farseer.modules["Frame Shift Drive"] == 5
    assert by["Colonel Bris Dekker"].star_pos == (0.0, 0.0, 0.0)
    assert by["Professor Palin"].base == "Abel Laboratory"  # after his move to Arque
    assert not by["Domino Green"].ship and by["Domino Green"].system == "Orishis"
    assert 21000 < math.dist(by["Mel Brandon"].star_pos, (0, 0, 0)) < 23000  # Colonia


def test_engineers_nearest_first_with_distance_and_jumps() -> None:
    rows = ui_engineers(SOL, 20.0)
    assert len(rows) == 38
    first = rows[0]
    assert (first.name, first.system, first.distance_ly, first.jumps) == (
        "Colonel Bris Dekker",
        "Sol",
        0.0,
        0,
    )
    assert [r.distance_ly for r in rows] == sorted(r.distance_ly or 0 for r in rows)
    qwent = next(r for r in rows if r.name == "Marco Qwent")  # Sirius, 8.59 ly
    assert (qwent.distance_ly, qwent.jumps) == (8.59, 1)
    assert [m.grade for m in first.modules] == sorted((m.grade for m in first.modules), reverse=True)


def test_engineers_without_a_position_are_by_name() -> None:
    rows = ui_engineers(None, None)
    assert all(r.distance_ly is None and r.jumps is None for r in rows)
    assert [r.name.casefold() for r in rows] == sorted(r.name.casefold() for r in rows)


# --- the station request ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("economy", "trader", "broker"),
    [
        ("Extraction", "Raw", ""),
        ("Refinery", "Raw", ""),
        ("Industrial", "Manufactured", "Human"),
        ("High Tech", "Encoded", "Guardian"),
        ("HighTech", "Encoded", "Guardian"),
        ("Military", "Encoded", ""),
        ("Colony", "", ""),
        ("", "", ""),
    ],
)
def test_kind_follows_the_primary_economy(economy: str, trader: str, broker: str) -> None:
    assert (trader_kind(economy), broker_kind(economy)) == (trader, broker)


def test_a_rows_kind_only_for_what_was_asked() -> None:
    assert row_kind((), "High Tech") == ""
    assert row_kind(TRADER, "High Tech") == "Encoded"
    assert row_kind(("Material Trader", "Technology Broker"), "High Tech") == "Encoded · Guardian"
    assert row_kind(("Technology Broker",), "Military") == ""


def test_kinds_ask_for_economies() -> None:
    assert ServiceRequest().economies() is None
    assert ServiceRequest(services=TRADER, trader_kind="Raw").economies() == ("Extraction", "Refinery")
    assert ServiceRequest(trader_kind="Raw").economies() is None  # no trader ticked: the kind does not count
    both = ServiceRequest(
        services=("Material Trader", "Technology Broker"), trader_kind="Encoded", broker_kind="Guardian"
    )
    assert both.economies() == ("High Tech",)
    never = ServiceRequest(
        services=("Material Trader", "Technology Broker"), trader_kind="Raw", broker_kind="Human"
    )
    assert never.economies() == ()  # no station can be both


def test_the_trader_form_is_the_recorded_one() -> None:
    q = _query("M", services=TRADER, trader_kind="Raw")
    assert (
        spansh.stations_form(q, 0)
        == json.loads((DATA / "spansh" / "stations_sol_trader_raw.json").read_text(encoding="utf-8"))[
            "request"
        ]["json"]
    )


def test_trader_rows_carry_their_kind() -> None:
    q = _query("M", services=TRADER, trader_kind="Raw")
    client = make_client(ScriptedTransport(reply("stations_sol_trader_raw")), FakeTime())
    rows = usable(spansh.stations_page(client, q, 0, threading.Event())[0], q)
    first = rows[0]
    assert (first.system, first.station, first.economy) == ("61 Cygni", "Broglie Terminal", "Refinery")
    ui = with_distances(UiServices(rows=ui_service_rows(TRADER, rows)), SOL, 10.0).rows
    assert {r.kind for r in ui} == {"Raw"}
    assert (ui[0].distance_ly, ui[0].jumps) == (round(math.dist(SOL, (first.x, first.y, first.z)), 2), 2)
    assert with_distances(UiServices(rows=ui), None, 10.0).rows[0].distance_ly is None


def test_usable_drops_small_pads_and_repeats_and_sorts() -> None:
    rows = [
        _row("far", 9),
        _row("small", 1, pad="M"),
        _row("near", 2),
        _row("near", 2),
        _row("none", 1, pad=""),
    ]
    assert [r.station for r in usable(rows, _query(pad="L"))] == ["near", "far"]
    assert [r.station for r in usable(rows, _query(pad="M"))] == ["small", "near", "far"]


def test_what_was_asked_in_words() -> None:
    assert what_asked(ServiceRequest("S")) == "a small pad"
    asked = ServiceRequest(
        "M", services=("Black Market", "Interstellar Factors Contact", "Material Trader"), trader_kind="Raw"
    )
    assert what_asked(asked) == "a medium pad, Black Market, Interstellar Factors and Material Trader (Raw)"


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ([], "send a JSON object"),
        ({"pad": "XL"}, "pad must be"),
        ({"pad": "L", "carriers": "yes"}, "carriers, settlements"),
        ({"pad": "L", "services": ["Bar"]}, "services must be"),
        ({"pad": "L", "services": "Refuel"}, "services must be"),
        ({"pad": "L", "trader_kind": "Shiny"}, "trader_kind must be"),
        ({"pad": "L", "broker_kind": "Alien"}, "broker_kind must be"),
    ],
)
def test_bad_requests(raw: object, error: str) -> None:
    got = parse_service_request(raw)
    assert isinstance(got, str) and got.startswith(error)


def test_good_requests() -> None:
    assert parse_service_request({"pad": "M"}) == ServiceRequest("M")
    got = parse_service_request(
        {"pad": "L", "carriers": True, "settlements": False, "hide_barred": False,
         "services": ["Repair", "Material Trader", "Refuel", "Repair"], "trader_kind": "Raw",
         "broker_kind": "Human"}
    )  # fmt: skip
    assert got == ServiceRequest(
        "L",
        carriers=True,
        settlements=False,
        hide_barred=False,
        services=("Refuel", "Repair", "Material Trader"),
        trader_kind="Raw",
    )  # fmt: skip  (catalog order, each once; no broker ticked: its kind is dropped)


# --- the plugin ---------------------------------------------------------------------------------------
def test_engineers_are_in_the_page_state(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.pump()
    state = json.loads(p.snapshot)["state"]
    assert len(state["engineers"]) == 38
    assert state["engineers"][0]["name"] == "Baltanos"  # no position yet: by name
    p.on_journal(
        {"event": "FSDJump", "StarSystem": "Sol", "SystemAddress": SOL_ID, "StarPos": [0, 0, 0]}, None
    )
    p.pump()
    first = json.loads(p.snapshot)["state"]["engineers"][0]
    assert (first["name"], first["system_id64"], first["distance_ly"]) == (
        "Colonel Bris Dekker",
        str(SOL_ID),
        0.0,
    )


def test_bad_service_request_is_refused(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    reply_ = p.api_post("/api/services", {"pad": "L", "services": ["Bar"]})
    assert reply_ is not None and reply_[0] == 400


def test_service_search_needs_a_position(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.api_post("/api/services", {"pad": "L", "services": list(TRADER)})
    p.pump()
    assert p.services.status == "error" and "jump once" in p.services.message
