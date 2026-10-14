"""Where to sell the hold: the hold from the journal and EDMC, the tonnes mined this session, Spansh's
sell search (recorded), the best offers, the one station for the whole load, and the plugin's card end to
end. No live calls."""

import json
import math
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.earnings import Earnings
from edmultitool.core.events import (
    CargoHeld,
    CargoItem,
    Refined,
    cargo_from_edmc_state,
    parse_cargo,
    parse_money,
)
from edmultitool.core.market import Standings
from edmultitool.core.sell import (
    Good,
    SellFound,
    SellQuery,
    SellRequest,
    hold,
    parse_sell_request,
    sell_result,
    ui_refined,
    usable,
)
from edmultitool.history import scan_earnings
from edmultitool.plugin import ScoutPlugin
from edmultitool.sell_lookup import find
from edmultitool.sources import spansh
from edmultitool.sources.http import Request, Response
from fakes import FakeTime, ScriptedTransport, make_client, recorded, reply

DATA = Path(__file__).parent / "data"
NOW = datetime(2026, 10, 14, 0, 55, tzinfo=UTC)  # the recorded search was made at 00:5x UTC
SOL = (0.0, 0.0, 0.0)
SOL_ID = 10477373803
LOAD = (Good("Painite", 30), Good("Platinum", 12), Good("Tritium", 4))

INVENTORY = [
    {"Name": "painite", "Count": 30, "Stolen": 0},
    {"Name": "platinum", "Count": 12, "Stolen": 0},
    {"Name": "lowtemperaturediamond", "Name_Localised": "Low Temperature Diamonds", "Count": 6, "Stolen": 0},
    {"Name": "gold", "Count": 3, "Stolen": 2},
    {"Name": "survivalequipment", "Name_Localised": "Survival Equipment", "MissionID": 7, "Count": 2},
    {"Name": "drones", "Name_Localised": "Limpet", "Count": 10, "Stolen": 0},
    {"Name": "unobtainium", "Name_Localised": "Unobtainium", "Count": 1},
]


def _query(goods: tuple[Good, ...] = LOAD, **req: Any) -> SellQuery:
    return SellQuery(SellRequest(**({"pad": "M"} | req)), goods, "Sol", SOL, NOW)


def _rows() -> tuple[Any, ...]:
    client = make_client(ScriptedTransport(reply("sell_sol_painite")), FakeTime())
    rows, total = spansh.sell_page(client, _query(), LOAD[0], 0, threading.Event())
    assert total == 554 and len(rows) == 25
    return rows


# --- the hold -----------------------------------------------------------------------------------------
def test_the_hold_from_a_cargo_entry_that_lists_it() -> None:
    held = parse_cargo({"event": "Cargo", "Vessel": "Ship", "Count": 64, "Inventory": INVENTORY})
    assert held is not None and len(held.items) == 7
    gold = next(i for i in held.items if i.symbol == "gold")
    assert (gold.count, gold.stolen, gold.mission) == (3, 2, False)
    assert next(i for i in held.items if i.symbol == "survivalequipment").mission
    assert parse_cargo({"event": "Cargo", "Vessel": "Ship", "Count": 64}) is None  # the list is in Cargo.json
    assert parse_cargo({"event": "Cargo", "Vessel": "SRV", "Count": 1, "Inventory": INVENTORY}) is None


def test_the_hold_from_edmc_state() -> None:
    raw = {"timestamp": "2026-10-14T13:45:00Z", "event": "Cargo", "Vessel": "Ship", "Inventory": INVENTORY}
    from_json = cargo_from_edmc_state({"CargoJSON": raw, "Cargo": {"painite": 1}})
    assert (
        from_json is not None and len(from_json.items) == 7
    )  # Cargo.json first: stolen and mission cargo apart
    plain = cargo_from_edmc_state({"Cargo": {"painite": 30, "drones": 10, "none": 0}})
    assert plain == CargoHeld((CargoItem("painite", "", 30), CargoItem("drones", "", 10)), detailed=False)
    assert cargo_from_edmc_state({}) is None


def test_what_is_searched_and_what_is_only_shown() -> None:
    goods, lines = hold(parse_cargo({"event": "Cargo", "Inventory": INVENTORY}))
    # one tonne of the gold is yours; limpets, mission cargo and what the catalog does not know: not searched
    assert goods == (
        Good("Painite", 30),
        Good("Platinum", 12),
        Good("Low Temperature Diamonds", 6),
        Good("Gold", 1),
    )
    notes = {line.name: line.note for line in lines}
    assert notes["Gold"] == "2 t stolen: only a black market buys that"
    assert notes["Limpets"] == "limpets: not for sale here"
    assert notes["Survival Equipment"] == "mission cargo: deliver it"
    assert notes["Unobtainium"] == "no market data for this"
    assert [line.name for line in lines][:3] == ["Painite", "Platinum", "Low Temperature Diamonds"]
    assert hold(None) == ((), ())


# --- what you mined this session ----------------------------------------------------------------------
def _refine(when: str, kind: str = "painite", local: str = "Painite") -> dict[str, Any]:
    return {"timestamp": when, "event": "MiningRefined", "Type": f"${kind}_name;", "Type_Localised": local}


def test_mined_this_session_and_per_hour() -> None:
    (event,) = parse_money(_refine("2026-10-14T12:10:00Z"))
    assert event == Refined("painite", "Painite", "2026-10-14T12:10:00Z")
    e = Earnings()
    for entry in [
        _refine("2026-10-13T20:00:00Z"),  # yesterday's session: not counted
        {"timestamp": "2026-10-14T12:00:00Z", "event": "LoadGame"},
        _refine("2026-10-14T12:10:00Z"),
        _refine("2026-10-14T12:40:00Z", "lowtemperaturediamond", "Low Temperature Diamonds"),
        _refine("2026-10-14T13:10:00Z"),
    ]:
        for ev in parse_money(entry):
            e.add(ev)
    r = ui_refined(e.refined, e.session_start, "2026-10-14T13:40:00Z")
    assert (r.tonnes, r.since) == (3, "2026-10-14T12:10:00Z")
    assert r.by == (("Painite", 2), ("Low Temperature Diamonds", 1))
    assert r.per_hour == 2.0  # 3 t in 1.5 h
    first = e.refined[:1]  # the session's first tonne alone
    assert ui_refined(first, e.session_start, "2026-10-14T12:12:00Z").per_hour is None  # 2 minutes: too short
    assert ui_refined(e.refined, "", "").tonnes == 0


def test_the_history_pass_reads_the_refined_tonnes(tmp_path: Path) -> None:
    lines = [{"timestamp": "2026-10-14T12:00:00Z", "event": "LoadGame"}, _refine("2026-10-14T12:10:00Z")]
    path = tmp_path / "Journal.2026-10-14T120000.01.log"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    e = scan_earnings(tmp_path, threading.Event())
    assert [r.symbol for r in e.refined] == ["painite"] and e.session_start == "2026-10-14T12:00:00Z"


# --- Spansh's sell search ----------------------------------------------------------------------------
def test_the_request_is_the_one_recorded() -> None:
    assert spansh.sell_form(_query(), LOAD[0], 0) == recorded("sell_sol_painite")["request"]["json"]
    large = spansh.sell_form(_query(pad="L", carriers=True, max_ly=20.0), LOAD[0], 1)
    assert large["filters"]["has_large_pad"] == {"value": True} and "type" not in large["filters"]
    assert large["filters"]["distance"] == {"min": "0", "max": "20"} and large["page"] == 1


def test_each_station_carries_its_prices_for_the_whole_hold() -> None:
    rows = _rows()
    jang = rows[0]
    assert (jang.system, jang.station, jang.pad) == ("San Tu", "Jang Chemical Installation", "L")
    assert [(o.commodity, o.price, o.demand) for o in jang.offers] == [
        ("Painite", 391116, 96),
        ("Platinum", 262732, 73),
    ]  # fmt: skip  — its Tritium demand is 0: not an offer
    assert all(r.offer("Painite") for r in rows)


def test_best_price_first_within_reach() -> None:
    rows = _rows()
    platinum = usable(rows, _query(), LOAD[1])
    assert [(r.system, r.station) for r in platinum[:3]] == [
        ("San Tu", "Chomsky Terminal"), ("Cranhcat", "Apt Landing"), ("LHS 6350", "Zaschka Point"),
    ]  # fmt: skip
    prices = [o.price for r in platinum if (o := r.offer("Platinum"))]
    assert prices == sorted(prices, reverse=True)
    demands = [o.demand for r in platinum if (o := r.offer("Platinum"))]
    assert min(demands) >= 12
    near = usable(rows, _query(max_ly=15.0), LOAD[0])
    assert near and all(math.dist(SOL, (r.x, r.y, r.z)) <= 15.0 for r in near)
    assert all(r.pad == "L" for r in usable(rows, _query(pad="L"), LOAD[0]))
    later = _query()
    later = SellQuery(later.request, later.goods, "Sol", SOL, NOW + timedelta(days=2))
    assert len(usable(rows, later, LOAD[0])) < len(usable(rows, _query(), LOAD[0]))  # older data left out


def _best_by_hand(rows: tuple[Any, ...], q: SellQuery, whole: bool) -> tuple[str, int]:
    """Every station that passes the filters for at least one commodity, totalled independently; `whole` =
    only those that buy all of the load."""
    best = ("", 0)
    pool = {(r.system, r.station): r for g in q.goods for r in usable(rows, q, g)}
    for (system, station), r in pool.items():
        total, taken = 0, 0
        for g in q.goods:
            o = r.offer(g.name)
            if o is not None and o.demand >= g.tonnes:
                total, taken = total + o.price * g.tonnes, taken + 1
        if (taken == len(q.goods) or not whole) and total > best[1]:
            best = (f"{system} / {station}", total)
    return best


def test_the_one_station_for_the_whole_load() -> None:
    rows, q = _rows(), _query()
    found = SellFound(tuple((g.name, tuple(usable(rows, q, g))) for g in q.goods))
    goods, best = sell_result(found, q, Standings(), frozenset({"Painite"}))
    assert best is not None
    # a station that buys all of it comes first, though Jang pays more for its Painite and Platinum alone
    assert (f"{best.place.system} / {best.place.station}", best.total) == _best_by_hand(rows, q, whole=True)
    assert _best_by_hand(rows, q, whole=False) == ("San Tu / Jang Chemical Installation", 14_886_264)
    assert best.place.station == "Borman Port" and best.left == ()
    # ... and the station that pays more for part of it is named beside it
    assert best.richer is not None and best.richer.place.station == "Jang Chemical Installation"
    assert (best.richer.total, best.richer.left) == (14_886_264, ("Tritium",))
    assert best.total == 247663 * 30 + 231861 * 12 + 57584 * 4 == 10_442_558
    assert [line.name for line in best.lines] == ["Painite", "Platinum", "Tritium"]
    # no station buys 40,000 t of Tritium: the one that pays most for part of the load
    part_q = SellQuery(q.request, (Good("Painite", 30), Good("Tritium", 40_000)), "Sol", SOL, NOW)
    part_found = SellFound(tuple((g.name, tuple(usable(rows, part_q, g))) for g in part_q.goods))
    _, part = sell_result(part_found, part_q, Standings())
    assert part is not None and part.place.station == "Jang Chemical Installation"
    assert (part.left, part.total) == (("Tritium",), 391116 * 30)
    assert part.richer is None  # it is the best payer already
    painite = next(g for g in goods if g.name == "Painite")
    assert painite.mined and len(painite.offers) == 3 and painite.offers[0].value == 391116 * 30
    assert [g.name for g in goods] == ["Painite", "Platinum", "Tritium"]  # biggest best value first


def test_stations_that_refuse_you_are_left_out() -> None:
    rows, q = _rows(), _query()
    found = SellFound(tuple((g.name, tuple(usable(rows, q, g))) for g in q.goods))
    hostile = Standings({"redshift logistics": -95.0})  # WISE 1506+7027: Borman Port, Parise Dock, …
    goods, best = sell_result(found, q, hostile)
    assert best is not None and (best.place.station, best.left) == ("Chomsky Terminal", ())
    assert all(o.place.system != "WISE 1506+7027" for g in goods for o in g.offers)
    assert next(g for g in goods if g.name == "Painite").barred == 3  # Parise Dock, Borman Port, Dobrovolskiy
    shown_q = SellQuery(SellRequest(pad="M", hide_barred=False), LOAD, "Sol", SOL, NOW)
    shown_goods, shown_best = sell_result(found, shown_q, hostile)
    painite = next(g for g in shown_goods if g.name == "Painite")
    assert [o.place.barred for o in painite.offers] == [False, True, True]  # shown, marked
    # the one stop is never a station that would refuse you, even when those are shown
    assert shown_best is not None and shown_best.place.station == "Chomsky Terminal"


def test_a_later_page_failing_keeps_the_stations_found() -> None:
    t = ScriptedTransport(reply("sell_sol_painite"), Response(500, b"{}"))
    client = make_client(t, FakeTime(), max_retries=0)
    q = _query(
        goods=(Good("Painite", 6000),)
    )  # one station of the first page buys that much: a second is asked
    found = find(client, q, Standings(), threading.Event())
    ((name, rows),) = found.by_good
    assert name == "Painite" and len(t.requests) == 2
    assert [(r.system, r.station) for r in rows] == [("2MASS 1503+2525", rows[0].station)] and rows[0].offer(
        "Painite"
    )


def test_bad_requests() -> None:
    good = {"max_ly": 50, "pad": "L", "carriers": False, "max_age_days": 3, "hide_barred": True}
    assert parse_sell_request(good) == SellRequest(50.0, "L", False, 3.0, True)
    assert isinstance(parse_sell_request(good | {"max_ly": 0}), str)
    assert isinstance(parse_sell_request(good | {"max_ly": 501}), str)
    assert isinstance(parse_sell_request(good | {"pad": "XL"}), str)
    assert isinstance(parse_sell_request(good | {"max_age_days": 31}), str)
    assert isinstance(parse_sell_request(good | {"carriers": "yes"}), str)
    assert isinstance(parse_sell_request([]), str)


# --- the plugin end to end --------------------------------------------------------------------------
GOOD = {"max_ly": 50, "pad": "M", "carriers": False, "max_age_days": 3, "hide_barred": True}


def _route(request: Request) -> Response:
    if "spansh" in request.url and request.url.endswith("/stations/search"):
        return reply("sell_sol_painite")  # each commodity's search: the rows carry the whole hold's prices
    return Response(404, b"{}")


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path)
    p.now = lambda: NOW
    for line in (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            p.on_journal(json.loads(line), None)
    p.on_journal({"timestamp": "2026-10-14T00:00:00Z", "event": "LoadGame"}, None)
    p.on_journal(
        {"timestamp": "2026-10-14T00:00:05Z", "event": "FSDJump", "StarSystem": "Sol",
         "SystemAddress": SOL_ID, "StarPos": [0, 0, 0]},
        None,
    )  # fmt: skip
    p.on_journal(_refine("2026-10-14T00:10:00Z"), None)
    inventory = [
        {"Name": "painite", "Count": 30},
        {"Name": "platinum", "Count": 12},
        {"Name": "tritium", "Count": 4},
        {"Name": "drones", "Count": 10},
    ]
    p.on_journal({"timestamp": "2026-10-14T00:30:00Z", "event": "Cargo", "Vessel": "Ship", "Count": 56,
                  "Inventory": inventory}, None)  # fmt: skip
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
    assert done(), p.sell


def test_search_shows_the_best_offers_and_the_one_stop(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    assert [line.name for line in p.ui.sell.hold] == ["Painite", "Platinum", "Tritium", "Limpets"]
    assert p.api_post("/api/sell", GOOD) == (202, {"accepted": "sell"})
    _pump_until(p, lambda: p.sell.status == "done")
    s = p.ui.sell
    assert len(t.requests) == 3  # one search per commodity
    assert s.handled == 1 and s.searched_from == "Sol" and not s.changed
    assert s.message.startswith("Best prices within 50 ly.")
    assert s.best is not None and (s.best.place.station, s.best.total) == ("Borman Port", 10_442_558)
    routing = p.ui.ranges.routing
    assert routing is not None and s.best.place.distance_ly == 10.52
    assert s.best.richer is not None and s.best.richer.place.distance_ly == 45.34  # measured too
    assert s.best.place.jumps == math.ceil(10.52 / routing)  # the fewest straight jumps at the routing range
    painite = next(g for g in s.goods if g.name == "Painite")
    assert painite.mined and s.refined.tonnes == 1
    state = json.loads(p.snapshot)["state"]["sell"]
    assert (
        state["best"]["total"] == 10_442_558 and state["goods"][0]["offers"][0]["place"]["system"] == "San Tu"
    )
    # a tonne more in the hold: the search is no longer for what you carry
    inventory = [
        {"Name": "painite", "Count": 31},
        {"Name": "platinum", "Count": 12},
        {"Name": "tritium", "Count": 4},
    ]
    p.on_journal(
        {"timestamp": "2026-10-14T00:40:00Z", "event": "Cargo", "Vessel": "Ship", "Inventory": inventory},
        None,
    )
    assert p.ui.sell.changed


def test_a_cargo_entry_without_the_list_takes_it_from_edmc(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, _t = plugin
    cargo_json = {"timestamp": "2026-10-14T00:41:00Z", "event": "Cargo", "Vessel": "Ship",
                  "Inventory": [{"Name": "painite", "Count": 31}]}  # fmt: skip
    p.on_journal({"timestamp": "2026-10-14T00:41:00Z", "event": "Cargo", "Vessel": "Ship", "Count": 31},
                 {"CargoJSON": cargo_json})  # fmt: skip
    assert [(line.name, line.tonnes) for line in p.ui.sell.hold] == [("Painite", 31)]
    p.on_journal({"timestamp": "2026-10-14T00:42:00Z", "event": "Cargo", "Vessel": "Ship", "Count": 31}, None)
    assert [(line.name, line.tonnes) for line in p.ui.sell.hold] == [("Painite", 31)]  # nothing new: kept


def test_nothing_to_sell_or_nowhere_known(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    assert p.api_post("/api/sell", GOOD | {"pad": "XL"}) == (400, {"error": "pad must be S, M or L"})
    p.api_post("/api/sell", GOOD)
    p.pump()
    assert p.sell.message.startswith("Where you are is not known yet")
    p.on_journal(
        {"event": "FSDJump", "StarSystem": "Sol", "SystemAddress": SOL_ID, "StarPos": [0, 0, 0]}, None
    )
    p.api_post("/api/sell", GOOD)
    p.pump()
    assert p.sell.message.startswith("What your hold holds is not known yet")
    p.on_journal(
        {"event": "Cargo", "Vessel": "Ship", "Count": 10, "Inventory": [{"Name": "drones", "Count": 10}]},
        None,
    )
    p.api_post("/api/sell", GOOD)
    p.pump()
    assert p.sell.status == "error" and p.sell.message == "Nothing in your hold to sell at a market."


def test_spansh_down_says_so(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    down = ScriptedTransport(Response(503, b"<html>maintenance</html>"), repeat_last=True)
    p.client = make_client(down, FakeTime(), max_retries=0)
    p.api_post("/api/sell", GOOD)
    _pump_until(p, lambda: p.sell.status == "error")
    assert "Spansh" in p.sell.message and p.sell.best is None


def test_searched_again_the_hold_is_no_longer_changed(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.api_post("/api/sell", GOOD)
    _pump_until(p, lambda: p.sell.status == "done")
    p.on_journal({"timestamp": "2026-10-14T00:40:00Z", "event": "Cargo", "Vessel": "Ship",
                  "Inventory": [{"Name": "painite", "Count": 31}]}, None)  # fmt: skip
    assert p.ui.sell.changed
    p.api_post("/api/sell", GOOD)
    _pump_until(p, lambda: p.sell.status == "done" and p.sell.handled == 2)
    assert not p.ui.sell.changed and p.ui.sell.searched_load == (("Painite", 31),)


def test_edmc_started_mid_session_knows_the_hold(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    cargo_json = {"event": "Cargo", "Vessel": "Ship", "Inventory": [{"Name": "painite", "Count": 8}]}
    p.on_journal({"event": "StartUp"}, {"CargoJSON": cargo_json, "Cargo": {"painite": 8}})
    assert [(line.name, line.tonnes) for line in p.ui.sell.hold] == [("Painite", 8)]
    assert not p.ui.sell.hold_rough


def test_a_hold_known_only_from_edmc_s_summary_says_so(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.on_journal({"event": "StartUp"}, {"Cargo": {"gold": 15}})  # 10 t of it may be mission cargo: not known
    assert [(line.name, line.tonnes) for line in p.ui.sell.hold] == [("Gold", 15)] and p.ui.sell.hold_rough
    p.on_journal({"event": "Cargo", "Vessel": "Ship", "Count": 15, "Inventory": [
        {"Name": "gold", "Count": 10, "MissionID": 3}, {"Name": "gold", "Count": 5},
    ]}, None)  # fmt: skip
    assert not p.ui.sell.hold_rough  # the game listed it: mission cargo apart again
    assert [line.note for line in p.ui.sell.hold] == ["", "mission cargo: deliver it"]


def test_at_most_twelve_commodities_are_searched() -> None:
    names = ["painite", "platinum", "gold", "silver", "palladium", "osmium", "tritium", "bertrandite",
             "indite", "gallite", "coltan", "uraninite", "lepidolite"]  # fmt: skip
    inventory = [{"Name": n, "Count": 20 - i} for i, n in enumerate(names)]
    goods, lines = hold(parse_cargo({"event": "Cargo", "Inventory": inventory}))
    assert len(goods) == 12 and goods[-1] == Good("Uraninite", 9)
    (cut,) = [line for line in lines if line.note]
    assert cut.name == "Lepidolite" and cut.note.startswith("not searched: 12 commodities at most")
    inventory[-1] = {"Name": "lepidolite", "Count": 8, "Stolen": 2}  # a stolen part keeps both notes
    _, lines = hold(parse_cargo({"event": "Cargo", "Inventory": inventory}))
    (cut,) = [line for line in lines if line.note]
    stolen, searched = cut.note.split("; ")
    assert stolen == "2 t stolen: only a black market buys that" and searched.startswith("not searched: 12")


def test_a_second_page_is_asked_when_the_first_has_only_stations_that_refuse_you() -> None:
    t = ScriptedTransport(reply("sell_sol_painite"), Response(500, b"{}"))
    client = make_client(t, FakeTime(), max_retries=0)
    q = _query(goods=(Good("Painite", 30),))
    hostile = {r.faction.casefold(): -95.0 for r in _rows() if r.faction}  # every station of page 0
    found = find(client, q, Standings(hostile), threading.Event())
    assert len(t.requests) == 2 and found.by_good[0][1]  # page 1 failed: page 0's rows still returned


def test_a_new_session_forgets_the_last_one_s_tonnes() -> None:
    e = Earnings()
    for entry in [
        _refine("2026-10-13T20:00:00Z"),
        {"timestamp": "2026-10-14T12:00:00Z", "event": "LoadGame"},
    ]:
        for ev in parse_money(entry):
            e.add(ev)
    assert e.refined == []


def test_each_refused_station_is_counted_once(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.factions.record(
        "Redshift Logistics"
    ).reputation = -95.0  # WISE 1506+7027's three stations, found by all three searches
    p.api_post("/api/sell", GOOD)
    _pump_until(p, lambda: p.sell.status == "done")
    assert p.sell.message.endswith(" 3 stations left out: they would refuse you docking.")
    assert p.sell.best is not None and p.sell.best.place.station == "Chomsky Terminal"
