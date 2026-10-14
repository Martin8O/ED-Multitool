"""Best trade from a station: the request, Spansh's trade router (recorded replies), and the plugin's
card end to end. No live calls."""

import json
import math
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.trade import TradeFound, TradeQuery, TradeRequest, parse_trade_request
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources import spansh
from edmultitool.sources.http import HttpError, Request, Response
from fakes import FakeTime, ScriptedTransport, make_client, network_down, recorded, reply

DATA = Path(__file__).parent / "data"
NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)

GOOD: dict[str, Any] = {
    "system": "Sol", "station": "Abraham Lincoln", "cargo": 400, "capital": 50_000_000, "max_hop_ly": 30,
    "max_arrival_ls": 5000, "max_age_days": 3, "pad": "L", "carriers": False, "planetary": True,
}  # fmt: skip


def _query(cargo: int = 400, capital: int = 50_000_000, **req: Any) -> TradeQuery:
    base = TradeRequest("Sol", "Abraham Lincoln", pad="L", max_hop_ly=30.0, max_age_days=3.0)
    return TradeQuery(replace(base, **req), cargo, capital)


def _trade(
    *steps: Response, cache_dir: Path | None = None, fake: FakeTime | None = None
) -> tuple[TradeFound, ScriptedTransport]:
    t = ScriptedTransport(*steps)
    client = make_client(t, fake or FakeTime(), cache_dir=cache_dir)
    found = spansh.trade(client, _query(), threading.Event())
    return found, t


# --- the request ---------------------------------------------------------------------------------------
def test_a_request_takes_the_page_fields_and_empty_hold_or_credits() -> None:
    assert parse_trade_request(GOOD) == TradeRequest(
        "Sol", "Abraham Lincoln", 400, 50_000_000, 30.0, 5000, 3.0, "L", False, True
    )
    tidy = parse_trade_request(GOOD | {"station": "  Abraham   Lincoln ", "cargo": None, "capital": None})
    assert isinstance(tidy, TradeRequest)
    assert (tidy.station, tidy.cargo, tidy.capital) == ("Abraham Lincoln", None, None)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"system": " "}, "name the system you buy in"),
        ({"station": ""}, "name the station you buy at"),
        ({"cargo": 0}, "cargo must be a whole number of tonnes from 1 to 10,000"),
        ({"cargo": 2.5}, "cargo must be a whole number of tonnes from 1 to 10,000"),
        ({"capital": True}, "credits must be a whole number above 0"),
        ({"max_hop_ly": 0}, "distance must be more than 0 and at most 1000 ly"),
        ({"max_arrival_ls": 0.5}, "arrival distance must be a whole number of ls above 0"),
        ({"max_age_days": 31}, "data age must be more than 0 and at most 30 days"),
        ({"pad": "XL"}, "pad must be S, M or L"),
        ({"planetary": "yes"}, "carriers and planetary must be true or false"),
    ],
)
def test_a_bad_request_is_refused(change: dict[str, Any], error: str) -> None:
    assert parse_trade_request(GOOD | change) == error


# --- Spansh's trade router -----------------------------------------------------------------------------
def test_the_trade_router_is_asked_what_was_recorded_live() -> None:
    sent = recorded("trade_submit")["request"]
    assert sent["url"] == "https://spansh.co.uk/api/trade/route"
    assert [tuple(f) for f in sent["form"]] == spansh.trade_form(_query())
    smaller = dict(spansh.trade_form(_query(pad="M", carriers=True, planetary=False, max_age_days=0.25)))
    assert smaller["requires_large_pad"] == "0" and smaller["allow_player_owned"] == "1"
    assert smaller["allow_planetary"] == "0" and smaller["max_price_age"] == "21600"


def test_one_hop_reads_both_stations_and_the_goods() -> None:
    found, t = _trade(reply("trade_submit"), reply("trade_done"))
    assert [r.url for r in t.requests][1].startswith("https://spansh.co.uk/api/results/")
    hop = recorded("trade_done")["body"]["result"][0]
    assert found.source is not None and found.destination is not None
    assert (found.source.system, found.source.station, found.source.system_id64) == (
        "Sol", "Abraham Lincoln", 10477373803,
    )  # fmt: skip
    dst = hop["destination"]
    assert (found.destination.system, found.destination.station) == (dst["system"], dst["station"])
    assert found.destination.arrival_ls == dst["distance_to_arrival"]
    assert found.destination.updated == datetime.fromtimestamp(dst["market_updated_at"], UTC).isoformat()
    assert found.distance_ly == hop["distance"] and found.total_profit == hop["total_profit"]
    (good,) = found.goods
    raw = hop["commodities"][0]
    assert (good.name, good.amount, good.profit, good.total_profit) == (
        raw["name"], raw["amount"], raw["profit"], raw["total_profit"],
    )  # fmt: skip
    assert good.buy_price == raw["source_commodity"]["buy_price"]
    assert good.sell_price == raw["destination_commodity"]["sell_price"]
    assert good.profit == good.sell_price - good.buy_price and good.total_profit == good.profit * good.amount


def test_credits_too_few_shrink_the_load_and_nothing_found_is_an_answer() -> None:
    poor, _ = _trade(reply("trade_submit"), reply("trade_poor"))
    (good,) = poor.goods
    assert good.amount == min(400, 20_000 // good.buy_price)  # as much as 20,000 Cr buys
    none, _ = _trade(reply("trade_submit"), reply("trade_none"))
    assert none == TradeFound()


def test_an_unknown_station_is_refused_in_spanshs_words() -> None:
    with pytest.raises(HttpError) as exc:
        _trade(reply("error_trade_station"))
    assert exc.value.status == 400 and exc.value.said == "Could not find station"


def test_the_same_trade_again_soon_comes_from_the_cache(tmp_path: Path) -> None:
    fake = FakeTime()
    first, t = _trade(reply("trade_submit"), reply("trade_done"), cache_dir=tmp_path, fake=fake)
    again, t2 = _trade(cache_dir=tmp_path, fake=fake)  # any call would fail
    assert again == first and len(t.requests) == 2 and t2.requests == []


# --- the plugin end to end -------------------------------------------------------------------------------
_asked: list[str] = []  # the stations submitted, newest last: "Lonely Port" gets the empty reply


def _route(request: Request) -> Response:
    if request.url.endswith("/trade/route"):
        station = dict(request.form or ()).get("station") or ""
        _asked.append(station)
        known = station in ("Abraham Lincoln", "Lonely Port")
        return reply("trade_submit" if known else "error_trade_station")
    if "/results/" in request.url:
        return reply("trade_none" if _asked[-1] == "Lonely Port" else "trade_done")
    return Response(404, b"{}")


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path)
    p.now = lambda: NOW
    for line in (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            p.on_journal(json.loads(line), None)
    p.on_journal(
        {"event": "FSDJump", "StarSystem": "Sol", "SystemAddress": 10477373803, "StarPos": [0, 0, 0]}, None
    )
    p.on_journal(
        {
            "timestamp": "2026-10-14T07:59:00Z",
            "event": "Docked",
            "StarSystem": "Sol",
            "StationName": "Abraham Lincoln",
        },
        None,
    )
    p.on_dashboard({"timestamp": "2026-10-14T08:00:00Z", "Balance": 50_000_000})
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
    assert done(), p.trade


def test_the_form_starts_where_you_docked_with_your_hold_and_balance(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    t = plugin[0].ui.trade
    assert (t.docked_system, t.docked_station, t.hold, t.balance) == ("Sol", "Abraham Lincoln", 8, 50_000_000)
    assert t.status == "idle" and t.source is None


def test_a_search_shows_the_trade_spansh_found(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, transport = plugin
    body = GOOD | {"cargo": None, "capital": None}
    assert p.api_post("/api/trade", body) == (202, {"accepted": "trade"})
    _pump_until(p, lambda: p.trade.status == "done")
    t = p.ui.trade
    hop = recorded("trade_done")["body"]["result"][0]
    assert t.handled == 1 and (t.cargo, t.capital) == (8, 50_000_000)  # the hold and balance filled in
    assert t.source is not None and t.destination is not None
    assert (t.destination.system, t.destination.station) == (
        hop["destination"]["system"],
        hop["destination"]["station"],
    )
    assert t.total_profit == hop["total_profit"] and t.distance_ly == round(hop["distance"], 2)
    good = t.goods[0]
    assert t.cost == good.amount * good.buy_price
    assert t.message == (
        f"Buy {good.name} at Abraham Lincoln, sell at {t.destination.station} ({t.destination.system}): "
        f"{hop['total_profit']:,} Cr profit."
    )
    # jumps with the load aboard: the range routes are planned with, counted with that cargo
    full_400 = replace(p.state, cargo=400).full_tank_range()
    assert full_400 is not None and t.laden_ly == round(full_400 * (1 - p.settings.margin_pct / 100), 2)
    assert t.distance_ly is not None and t.jumps == math.ceil(t.distance_ly / t.laden_ly) >= 1
    form = dict(transport.requests[0].form or ())
    assert form["max_cargo"] == "8" and form["starting_capital"] == "50000000"
    snapshot = json.loads(p.snapshot)["state"]["trade"]
    assert snapshot["destination"]["system_id64"] == str(hop["destination"]["system_id64"])
    assert snapshot["goods"][0]["total_profit"] == hop["commodities"][0]["total_profit"]


def test_an_unknown_station_says_to_check_its_name(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.api_post("/api/trade", GOOD | {"station": "Nonexistent Port"})
    _pump_until(p, lambda: p.trade.status == "error")
    assert p.trade.message == (
        "Spansh's trade router does not know Nonexistent Port in Sol, or knows no market there: check the "
        "station's name, or start from another station."
    )


def test_jumps_count_the_load_even_when_routes_use_the_empty_best_case(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, _t = plugin
    p.set_settings(replace(p.settings, route_basis="best"))
    p.api_post("/api/trade", GOOD)
    _pump_until(p, lambda: p.trade.status == "done")
    best = p.ui.ranges.routing
    assert best is not None and p.trade.laden_ly is not None and p.trade.laden_ly < best
    full_400 = replace(p.state, cargo=400).full_tank_range()
    assert full_400 is not None and p.trade.laden_ly == round(full_400 * (1 - p.settings.margin_pct / 100), 2)


def test_nothing_found_and_an_error_clear_the_trade_shown(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, _t = plugin
    p.api_post("/api/trade", GOOD)
    _pump_until(p, lambda: p.trade.status == "done" and p.trade.source is not None)
    p.api_post("/api/trade", GOOD | {"station": "Nonexistent Port"})
    _pump_until(p, lambda: p.trade.status == "error")
    assert p.trade.source is None and p.trade.goods == () and p.trade.total_profit == 0
    p.api_post("/api/trade", GOOD | {"station": "Lonely Port", "max_hop_ly": 12.5})
    _pump_until(p, lambda: p.trade.status == "done")
    assert p.trade.source is None and p.trade.jumps is None and p.trade.handled == 3
    assert p.trade.message == (
        "No trade from Lonely Port pays within 12.5 ly with these filters: try a longer distance, older data "
        "or a longer arrival distance."
    )


def test_a_balance_of_zero_and_a_ship_without_cargo_racks_say_so(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.on_dashboard({"timestamp": "2026-10-14T08:00:00Z", "Balance": 0})
    p.api_post("/api/trade", GOOD | {"capital": None})
    p.pump()
    assert p.trade.message == "Your balance is 0 Cr: enter the credits to spend."
    p.on_journal(
        {
            "timestamp": "2026-10-14T08:01:00Z", "event": "Loadout", "Ship": "sidewinder", "ShipID": 1,
            "CargoCapacity": 0, "UnladenMass": 25, "FuelCapacity": {"Main": 2, "Reserve": 0.3}, "Modules": [],
        },
        None,
    )  # fmt: skip
    p.api_post("/api/trade", GOOD | {"cargo": None})
    p.pump()
    assert (
        p.ui.trade.hold == 0 and p.trade.message == "Your ship has no cargo racks: enter the tonnes to fill."
    )


def test_bad_requests_and_no_hold_known(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    assert p.api_post("/api/trade", GOOD | {"pad": "XL"}) == (400, {"error": "pad must be S, M or L"})
    assert p._commands.empty()
    p.api_post("/api/trade", GOOD | {"cargo": None})
    p.pump()
    assert p.trade.status == "error" and p.trade.message.startswith("Your ship's cargo hold is not known yet")
    p.api_post("/api/trade", GOOD | {"capital": None})
    p.pump()
    assert p.trade.message.startswith("Your balance is not known yet") and p.trade.handled == 2


# --- the station choice ----------------------------------------------------------------------------------
def test_the_station_choice_lists_the_markets_of_the_system_nearest_first() -> None:
    t = ScriptedTransport(reply("trade_stations_sol"))
    stations = spansh.market_stations(make_client(t, FakeTime()), "Sol", threading.Event()) or ()
    assert recorded("trade_stations_sol")["request"]["json"] == json.loads(t.requests[0].json_body or "{}")
    raw = recorded("trade_stations_sol")["body"]["results"][0]["stations"]
    assert len(stations) == sum(1 for r in raw if r.get("has_market") is True) < len(raw)
    assert "Abraham Lincoln" in [st.name for st in stations] and all(st.market for st in stations)
    ls = [st.arrival_ls or 0.0 for st in stations]
    assert ls == sorted(ls)


def test_no_such_system_is_not_a_system_without_markets(tmp_path: Path) -> None:
    empty = Response(200, json.dumps({"results": [], "count": 0}).encode())
    t = ScriptedTransport(empty)
    assert spansh.market_stations(make_client(t, FakeTime()), "Nowhere", threading.Event()) is None
    bare = {
        "id64": 1,
        "name": "Barren",
        "x": 0,
        "y": 0,
        "z": 0,
        "stations": [{"name": "Hut", "has_market": False}],
    }
    known = Response(200, json.dumps({"results": [bare], "count": 1}).encode())
    p = ScoutPlugin(tmp_path)
    p.client = make_client(ScriptedTransport(known), FakeTime())
    assert p.api_get("/api/stations", {"system": "Barren"}) == (200, {"system": "Barren", "stations": []})


def test_the_page_asks_the_plugin_for_the_stations(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    assert p.api_get("/api/stations", {"system": "Sol"}) == (503, {"error": "online lookups are not running"})
    p.client = make_client(
        ScriptedTransport(reply("trade_stations_sol"), network_down()), FakeTime(), max_retries=0
    )
    status, body = p.api_get("/api/stations", {"system": "Sol"}) or (0, {})
    doc: dict[str, Any] = body  # type: ignore[assignment]
    assert status == 200 and doc["system"] == "Sol"
    first = doc["stations"][0]
    assert set(first) == {"name", "type", "pad", "arrival_ls"}
    assert "Abraham Lincoln" in [s["name"] for s in doc["stations"]]
    assert p.api_get("/api/stations", {"system": " "}) == (400, {"error": "name a system"})
    p.client = make_client(
        ScriptedTransport(reply("trade_stations_sol"), Response(200, b'{"results": []}'), network_down()),
        FakeTime(),
        max_retries=0,
    )
    p.api_get("/api/stations", {"system": "Sol"})
    assert p.api_get("/api/stations", {"system": "sol"}) == (
        404, {"error": "Spansh knows no system spelled 'sol': pick it from the suggestions"},
    )  # fmt: skip
    status, body = p.api_get("/api/stations", {"system": "Achenar"}) or (0, {})
    assert status == 502 and str(body).startswith("{'error': 'Spansh: could not connect")
