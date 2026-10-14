"""Nearest commodity market: the request, Spansh's and Ardent's replies (recorded), ED Multitool's own
filters, standings from the journal, and the plugin's card end to end. No live calls."""

import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from edmultitool.catalog.commodities import COMMODITIES, STATION_TYPES
from edmultitool.catalog.ships import ship_pad
from edmultitool.core.events import Arrived, PowerPledged, parse_event
from edmultitool.core.market import (
    MarketQuery,
    MarketRequest,
    MarketRow,
    Standings,
    UiMarket,
    keep,
    nearest,
    parse_market_request,
    split,
    standing,
    ui_rows,
    with_distances,
)
from edmultitool.history import scan_standings
from edmultitool.market_lookup import find
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources import ardent, spansh
from edmultitool.sources.http import Request, Response, SourceError
from fakes import FakeTime, ScriptedTransport, make_client, reply

DATA = Path(__file__).parent / "data"
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
SOL = (0.0, 0.0, 0.0)
SOL_ID = 10477373803


def _ardent() -> Response:
    doc = json.loads((DATA / "ardent" / "market_sol_tritium.json").read_text(encoding="utf-8"))
    return Response(doc["status"], json.dumps(doc["body"]).encode("utf-8"))


def _query(**req: Any) -> MarketQuery:
    base: dict[str, Any] = {"commodity": "Tritium", "side": "sell", "pad": "L", "carriers": True}
    return MarketQuery(MarketRequest(**(base | req)), "Sol", SOL_ID, SOL, NOW)


def _spansh_rows() -> tuple[MarketRow, ...]:
    client = make_client(ScriptedTransport(reply("market_sol_tritium")), FakeTime())
    rows, total = spansh.market_page(client, _query(), 0, threading.Event())
    assert total == 5188 and len(rows) == 25
    return rows


def _row(name: str, x: float = 1.0, **kw: Any) -> MarketRow:
    base: dict[str, Any] = {
        "station": name, "station_type": "Coriolis Starport", "system": f"Sys {name}", "system_id64": 1,
        "x": x, "y": 0.0, "z": 0.0, "arrival_ls": 100.0, "pad": "L", "carrier": False, "access": "",
        "faction": "Some Faction", "price": 50_000, "quantity": 500, "updated": "2026-10-12T10:00:00Z",
    }  # fmt: skip
    return MarketRow(**(base | kw))


# --- standings ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("rep", "name"),
    [
        (-100, "hostile"), (-90.01, "hostile"), (-90, "unfriendly"), (-35, "unfriendly"), (-34.9, "neutral"),
        (4, "neutral"), (4.01, "cordial"), (35, "cordial"), (35.5, "friendly"), (90, "friendly"),
        (90.1, "allied"), (None, ""),
    ],
)  # fmt: skip
def test_reputation_bands_are_the_journal_manuals(rep: float | None, name: str) -> None:
    assert standing(rep) == name


def test_arrivals_carry_your_reputation_and_powerplay_entries_your_pledge() -> None:
    entry = {
        "event": "FSDJump", "StarSystem": "Sol", "SystemAddress": SOL_ID, "StarPos": [0, 0, 0],
        "Factions": [{"Name": "Mother Gaia", "MyReputation": -95.5}, {"Name": "Sol Workers' Party"}, "junk"],
    }  # fmt: skip
    ev = parse_event(entry)
    assert isinstance(ev, Arrived) and ev.factions == (("Mother Gaia", -95.5),)
    assert parse_event({"event": "Powerplay", "Power": "Li Yong-Rui"}) == PowerPledged("Li Yong-Rui")
    assert parse_event({"event": "PowerplayDefect", "FromPower": "A", "ToPower": "Felicia Winters"}) == (
        PowerPledged("Felicia Winters")
    )
    assert parse_event({"event": "PowerplayLeave", "Power": "Li Yong-Rui"}) == PowerPledged("")


def test_history_keeps_the_newest_reputation_and_pledge(tmp_path: Path) -> None:
    def arrive(rep: float) -> str:
        return json.dumps({
            "event": "Location", "StarSystem": "Sol", "SystemAddress": SOL_ID, "StarPos": [0, 0, 0],
            "Factions": [{"Name": "Mother Gaia", "MyReputation": rep}],
        })  # fmt: skip

    old = tmp_path / "Journal.2026-10-01T100000.01.log"
    new = tmp_path / "Journal.2026-10-02T100000.01.log"
    old.write_text(arrive(10) + "\n" + json.dumps({"event": "Powerplay", "Power": "Aisling Duval"}) + "\n")
    new.write_text(
        json.dumps({"event": "PowerplayJoin", "Power": "Li Yong-Rui"}) + "\n" + arrive(-95) + "\n{half"
    )
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    factions, power = scan_standings(tmp_path, threading.Event())
    assert factions.reputation() == {"mother gaia": -95} and power == "Li Yong-Rui"


# --- the request ---------------------------------------------------------------------------------------
GOOD = {
    "commodity": "tritium", "side": "buy", "min_qty": 10, "pad": "M", "carriers": False, "max_age_days": 3,
    "hide_barred": True,
}  # fmt: skip


def test_a_request_takes_the_catalogs_spelling() -> None:
    assert parse_market_request(GOOD) == MarketRequest("Tritium", "buy", 10, "M", False, 3.0, True)
    assert parse_market_request(GOOD | {"commodity": "  low temperature   DIAMONDS "}) == MarketRequest(
        "Low Temperature Diamonds", "buy", 10, "M", False, 3.0, True
    )


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"commodity": "Unobtainium"}, "pick a commodity from the suggestions"),
        ({"side": "steal"}, "side must be buy or sell"),
        ({"pad": "XL"}, "pad must be S, M or L"),
        ({"min_qty": True}, "least amount must be a whole number of tonnes"),
        ({"min_qty": 1.5}, "least amount must be a whole number of tonnes"),
        ({"max_age_days": 0}, "data age must be more than 0 and at most 30 days"),
        ({"max_age_days": 31}, "data age must be more than 0 and at most 30 days"),
        ({"carriers": "yes"}, "carriers and hide_barred must be true or false"),
    ],
)
def test_a_bad_request_is_refused(change: dict[str, Any], error: str) -> None:
    assert parse_market_request(GOOD | change) == error


def test_the_catalog_pairs_names_with_ardent_symbols_and_leaves_carriers_out() -> None:
    assert COMMODITIES["Tritium"] == "tritium"
    assert COMMODITIES["Low Temperature Diamonds"] == "lowtemperaturediamond"
    assert COMMODITIES["Void Opal"] == "opal"
    assert "Drake-Class Carrier" not in STATION_TYPES and "Coriolis Starport" in STATION_TYPES
    assert (ship_pad("mediumtransport01"), ship_pad("cutter"), ship_pad("sidewinder"), ship_pad("new")) == (
        "M", "L", "S", "L",
    )  # fmt: skip


# --- Spansh -----------------------------------------------------------------------------------------
def test_market_search_asks_what_was_recorded_live() -> None:
    form = spansh.market_form(_query(), 0)
    recorded = json.loads((DATA / "spansh" / "market_sol_tritium.json").read_text(encoding="utf-8"))
    sent = recorded["request"]["json"]
    assert form["filters"]["market"] == sent["filters"]["market"]
    assert form.keys() == sent.keys() and form["filters"].keys() == sent["filters"].keys()
    assert form["filters"]["market_updated_at"]["value"] == ["2026-10-09T16:00:00Z", "2026-10-13T16:00:00Z"]
    off = spansh.market_form(_query(carriers=False, pad="M", side="buy", min_qty=0), 2)
    assert off["filters"]["type"] == {"value": list(STATION_TYPES)} and "has_large_pad" not in off["filters"]
    assert off["filters"]["market"][0]["supply"]["value"][0] == 1 and off["page"] == 2


def test_market_rows_read_carriers_strongholds_and_factions() -> None:
    rows = {(r.system, r.station): r for r in _spansh_rows()}
    aisling = rows[("Alpha Centauri", "Stronghold Carrier")]
    assert aisling.stronghold and aisling.power == "Aisling Duval" and aisling.faction == ""
    carrier = rows[("Wolf 359", "T5M-47Y")]
    assert carrier.carrier and carrier.access == "squadron friends" and carrier.faction == ""
    lalande = rows[("Lalande 21185", "Beveridge Enterprise")]
    assert (lalande.faction, lalande.price, lalande.quantity, lalande.pad) == (
        "Earth Defense Fleet",
        53540,
        5958,
        "L",
    )
    assert lalande.updated == "2026-10-11T23:46:12Z"


def test_own_filters_drop_closed_carriers_strongholds_and_hostile_factions() -> None:
    you = Standings({"earth defense fleet": -95.0}, "Li Yong-Rui")
    kept, barred = keep(_spansh_rows(), _query(), you)
    names = [r.station for r in kept]
    assert "T5M-47Y" not in names and "V9T-W9G" not in names  # squadron / friends only
    assert {"T5M-47Y", "V9T-W9G"} <= {r.station for r in barred}  # counted as "would refuse you"
    assert "HFB-33L" in names and "VNG-03Q" in names  # open to all / access not known
    assert ("Sirius", "Stronghold Carrier") in [(r.system, r.station) for r in kept]  # your power's
    assert {(r.system, r.station) for r in barred} >= {
        ("Alpha Centauri", "Stronghold Carrier"), ("Lalande 21185", "Beveridge Enterprise"),
    }  # fmt: skip
    assert all(r.faction != "Earth Defense Fleet" for r in kept)
    shown, _ = keep(_spansh_rows(), _query(hide_barred=False), you)
    assert len(shown) == len(kept) + len(barred)
    no_carriers, _ = keep(_spansh_rows(), _query(carriers=False), you)
    assert not any(r.carrier for r in no_carriers)


def test_pad_amount_age_and_duplicates_are_checked_and_rows_sorted_by_distance() -> None:
    rows = [
        _row("far", x=9), _row("near", x=2), _row("small pad", pad="M"), _row("low", quantity=9),
        _row("old", updated="2026-10-09T15:59:59Z"), _row("no price", price=0), _row("near", x=2),
        _row("bad time", updated="yesterday"),
    ]  # fmt: skip
    kept, barred = keep(rows, _query(min_qty=10), Standings())
    assert [r.station for r in kept] == ["near", "far"] and barred == []
    kept, _ = keep(rows, _query(min_qty=10, pad="M"), Standings())
    assert [r.station for r in kept] == ["small pad", "near", "far"]


def test_barred_stations_are_counted_only_up_to_the_last_one_shown() -> None:
    q = MarketQuery(MarketRequest("Tritium", "sell"), "Sol", SOL_ID, SOL, NOW, count=2)
    kept = [_row("a", x=1), _row("b", x=2), _row("c", x=3)]
    barred = [_row("h1", x=1.5), _row("h2", x=5)]
    result = nearest(q, kept, barred, "Spansh")
    assert [r.station for r in result.rows] == ["a", "b"] and result.barred == 1
    assert nearest(q, kept[:1], barred, "Spansh").barred == 2


def test_pages_are_asked_until_enough_rows_are_kept() -> None:
    def page(request: Request) -> Response:
        doc = json.loads(request.json_body or "{}")
        body = json.loads(reply("market_sol_tritium").body)
        return Response(200, json.dumps(body | {"count": 60, "page": doc["page"]}).encode())

    t = ScriptedTransport(page, repeat_last=True)
    client = make_client(t, FakeTime())
    you = Standings({}, "")
    # 7 recorded stations hold ≥ 10,000 t; every later page repeats them, so all three pages are asked
    result = find(client, _query(min_qty=10_000), you, threading.Event())
    assert [json.loads(r.json_body or "{}")["page"] for r in t.requests] == [0, 1, 2]
    assert result.source == "Spansh" and len(result.rows) == 7


def test_ardent_answers_when_spansh_fails() -> None:
    def route(request: Request) -> Response:
        if "spansh" in request.url:
            return Response(500, b'{"error": "boom"}')
        return _ardent()

    t = ScriptedTransport(route, repeat_last=True)
    client = make_client(t, FakeTime(), max_retries=0)
    result = find(client, _query(carriers=False), Standings({}, "Li Yong-Rui"), threading.Event())
    assert result.source == "Ardent" and result.note == "Spansh: the site is down or overloaded (HTTP 500)"
    assert all(r.quantity >= 10 and not r.carrier and r.faction == "" for r in result.rows)
    kept, barred = split(list(result.rows), _query().request, Standings({}, "Li Yong-Rui"))
    assert [r.system for r in kept] == ["Lalande 21185", "Ross 248", "EZ Aquarii", "Procyon"]
    # Ardent's Stronghold Carriers name no power: never known to be yours
    assert [r.system for r in barred] == ["Alpha Centauri", "Sirius"] and all(r.stronghold for r in barred)
    url = t.requests[-1].url
    assert url.endswith(f"/v2/system/address/{SOL_ID}/commodity/name/tritium/nearby/imports")
    params = dict(t.requests[-1].params)
    assert params == {"maxDistance": "100", "minVolume": "10", "maxDaysAgo": "3", "sort": "distance",
                      "fleetCarriers": "false"}  # fmt: skip
    with_carriers = dict(ardent.nearby_form(_query(carriers=True)))
    assert "fleetCarriers" not in with_carriers  # "true" would mean carriers ONLY


def test_ardent_rows_read_like_spanshs() -> None:
    client = make_client(ScriptedTransport(_ardent()), FakeTime())
    rows = ardent.nearby(client, _query(), threading.Event())
    hutton = next(r for r in rows if r.station == "Hutton Orbital")
    assert (hutton.station_type, hutton.pad, hutton.updated) == ("Outpost", "M", "2026-10-11T13:52:37Z")
    stronghold = next(r for r in rows if r.station == "Stronghold Carrier")
    assert (
        stronghold.stronghold and stronghold.power == "" and stronghold.station_type == "Stronghold Carrier"
    )


def test_both_failing_says_both() -> None:
    t = ScriptedTransport(Response(500, b'{"error": "boom"}'), repeat_last=True)
    client = make_client(t, FakeTime(), max_retries=0)
    both = r"^the site is down or overloaded \(HTTP 500\), and the backup Ardent too; try again later$"
    with pytest.raises(SourceError, match=both):
        find(client, _query(), Standings(), threading.Event())


def test_a_commodity_ardent_does_not_know_fails_plainly() -> None:
    q = MarketQuery(MarketRequest("Guardian Casket", "sell"), "Sol", SOL_ID, SOL, NOW)
    with pytest.raises(SourceError, match="Ardent has no name for Guardian Casket"):
        ardent.nearby(make_client(ScriptedTransport(), FakeTime()), q, threading.Event())


def test_distances_and_jumps_follow_you() -> None:
    result = nearest(_query(), [_row("a", x=30.0)], [], "Spansh")
    market = UiMarket(status="done", rows=ui_rows(result, Standings({"some faction": 50}, "")))
    (row,) = with_distances(market, (0.0, 0.0, 0.0), 14.0).rows
    assert (row.distance_ly, row.jumps, row.standing, row.barred) == (30.0, 3, "friendly", False)
    (row,) = with_distances(market, (30.0, 0.0, 0.0), 14.0).rows
    assert (row.distance_ly, row.jumps) == (0.0, 0)
    (row,) = with_distances(market, None, 14.0).rows
    assert (row.distance_ly, row.jumps) == (None, None)


# --- the plugin end to end --------------------------------------------------------------------------
def _route(request: Request) -> Response:
    if "spansh" in request.url and request.url.endswith("/stations/search"):
        return reply("market_sol_tritium")
    return Response(404, b"{}")


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path)
    p.now = lambda: NOW
    lines = (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines:
        if line.strip():
            p.on_journal(json.loads(line), None)
    p.on_journal({"event": "Powerplay", "Power": "Li Yong-Rui"}, None)
    p.on_journal(
        {
            "event": "FSDJump", "StarSystem": "Sol", "SystemAddress": SOL_ID, "StarPos": [0, 0, 0],
            "Factions": [{"Name": "Earth Defense Fleet", "MyReputation": -91.0}],
        },
        None,
    )  # fmt: skip
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
    assert done(), p.market


def test_search_shows_the_nearest_stations_you_may_dock_at(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, _t = plugin
    assert p.ui.ship_pad == "M"  # the Lynx Highliner of the sample journal
    req = GOOD | {"commodity": "Tritium", "side": "sell", "pad": "L", "carriers": True}
    assert p.api_post("/api/market", req) == (202, {"accepted": "market"})
    _pump_until(p, lambda: p.market.status == "done")
    m = p.ui.market
    assert m.handled == 1 and m.source == "Spansh" and m.searched_from == "Sol" and len(m.rows) == 10
    assert m.message.startswith("10 stations that buy Tritium, nearest first.")
    # Earth Defense Fleet (Hostile) x2, Aisling Duval's Stronghold Carrier, two carriers closed to visitors
    assert "5 stations hidden: they would refuse you docking." in m.message
    names = [(r.system, r.station) for r in m.rows]
    assert ("Lalande 21185", "Beveridge Enterprise") not in names  # Earth Defense Fleet: Hostile
    assert ("Alpha Centauri", "Stronghold Carrier") not in names  # Aisling Duval's
    sirius = next(r for r in m.rows if r.system == "Sirius")
    assert sirius.power == "Li Yong-Rui" and not sirius.barred
    first = m.rows[0]
    assert (first.system, first.distance_ly, first.jumps) == ("Alpha Centauri", 4.38, 1)
    assert [r.distance_ly for r in m.rows] == sorted(r.distance_ly or 0 for r in m.rows)
    snapshot = json.loads(p.snapshot)["state"]["market"]
    assert snapshot["rows"][0]["system_id64"] == str(first.system_id64) and snapshot["barred"] == 5


def test_hostile_stations_shown_when_asked(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.api_post("/api/market", GOOD | {"side": "sell", "pad": "L", "carriers": True, "hide_barred": False})
    _pump_until(p, lambda: p.market.status == "done")
    lalande = next(r for r in p.market.rows if r.system == "Lalande 21185")
    assert lalande.standing == "hostile" and lalande.barred and p.market.barred == 0


def test_commodity_names_bad_requests_and_no_position(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    assert p.api_get("/api/commodities", {}) == (200, {"names": list(COMMODITIES)})
    assert p.api_post("/api/market", GOOD | {"commodity": "x"}) == (
        400, {"error": "pick a commodity from the suggestions"},
    )  # fmt: skip
    assert p._commands.empty()
    p.api_post("/api/market", GOOD)
    p.pump()
    assert p.market.status == "error" and p.market.message.startswith("Where you are is not known yet")


def test_the_same_search_again_soon_is_answered_from_the_cache(tmp_path: Path) -> None:
    t = ScriptedTransport(reply("market_sol_tritium"))
    client = make_client(t, FakeTime(), cache_dir=tmp_path)
    first = MarketQuery(MarketRequest("Tritium", "sell"), "Sol", SOL_ID, SOL, NOW)
    again = MarketQuery(
        MarketRequest("Tritium", "sell"), "Sol", SOL_ID, SOL, NOW.replace(minute=7, second=31)
    )
    rows, _ = spansh.market_page(client, first, 0, threading.Event())
    rows_again, _ = spansh.market_page(client, again, 0, threading.Event())  # no second call scripted
    assert rows == rows_again and len(t.requests) == 1


def test_a_later_page_failing_keeps_the_stations_found() -> None:
    t = ScriptedTransport(reply("market_sol_tritium"), Response(500, b"{}"))
    client = make_client(t, FakeTime(), max_retries=0)
    found = find(client, _query(min_qty=10_000), Standings(), threading.Event())
    assert found.source == "Spansh" and len(found.rows) == 7 and len(t.requests) == 2


def test_no_stock_or_demand_is_never_a_place_to_trade() -> None:
    kept, _ = keep([_row("empty", quantity=0), _row("some", quantity=1)], _query(min_qty=0), Standings())
    assert [r.station for r in kept] == ["some"]


def test_who_may_dock_is_decided_when_the_search_finishes(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, _t = plugin
    p.factions.records.clear()  # the history pass has not told yet when the search starts
    p.api_post("/api/market", GOOD | {"side": "sell", "pad": "L", "carriers": True})
    p.pump()
    p.factions.record("Earth Defense Fleet").reputation = -95.0  # ... and tells before the answer is shown
    _pump_until(p, lambda: p.market.status == "done")
    assert all(r.faction != "Earth Defense Fleet" for r in p.market.rows)
