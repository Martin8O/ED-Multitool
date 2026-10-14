"""Spansh adapter: the only place that knows Spansh's raw JSON. Request shapes we rely on are recorded in
docs/api-notes.md; replies come back as the typed models of `edmultitool.core.scout`.

Jobs (riches / exobiology tours, galaxy-plotter routes): POST the query → 202 {"job"} → GET
/results/{job} until it stops answering 202. The client's pacing keeps polling at ≤ 1/s. Completed
results are cached by query.
"""

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from edmultitool.catalog.commodities import STATION_TYPES
from edmultitool.catalog.genera import genus_name
from edmultitool.catalog.powers import CONTROLLED_STATES, POWERS
from edmultitool.core.arrival import SystemBody
from edmultitool.core.gear import StationInfo, SystemInfo
from edmultitool.core.market import MarketQuery, MarketRow
from edmultitool.core.nearby import SETTLEMENT_TYPES, ServiceQuery, ServiceRow
from edmultitool.core.plot import PlotQuery, PlotResult, Waypoint
from edmultitool.core.powerplay import POWER_SHOWN, PowerFound, PowerQuery, PowerRequest, PowerRow
from edmultitool.core.scout import (
    BioSpecies,
    ScoutBody,
    ScoutKind,
    ScoutQuery,
    ScoutResult,
    ScoutSystem,
    SystemHit,
)
from edmultitool.core.sell import Good, Offer, SellQuery, SellStation
from edmultitool.core.trade import TradeFound, TradeGood, TradeQuery, TradeStation
from edmultitool.sources.http import BadReply, HttpError, PoliteClient, Response, SourceError

BASE = "https://spansh.co.uk/api"
JOB_PATHS: dict[ScoutKind, str] = {"riches": "riches/route", "bio": "exobiology/route"}
PLOT_PATH = "generic/route"  # Spansh's galaxy plotter
TRADE_PATH = "trade/route"  # Spansh's trade router
NAMES_TTL = 24 * 3600.0
RESULT_TTL = 6 * 3600.0
JOB_DEADLINE = 300.0
SEARCH_TTL = 24 * 3600.0  # system / station searches by name (gear posts)
MAX_SEARCH_NAMES = 20
MARKET_TTL = 10 * 60.0  # markets change: a search is asked again after 10 minutes
MARKET_PAGE = 25  # stations per market search page
MAX_AMOUNT = 999_999_999
POWER_TTL = 15 * 60.0  # Powerplay standings move all week: a search is asked again after 15 minutes
BODIES_TTL = 3600.0  # a system's bodies: other explorers' scans reach Spansh within the hour
BODIES_PAGE = 100  # bodies per body-search page
MAX_BODY_PAGES = 5  # 500 bodies: more than any real system has
CACHE_MAX_AGE = max(NAMES_TTL, RESULT_TTL)


def system_names(client: PoliteClient, text: str, cancel: threading.Event) -> tuple[SystemHit, ...]:
    """Systems whose name starts with `text`, as Spansh's own autocomplete returns them."""
    text = text.strip()
    if len(text) < 2:
        return ()
    resp = client.get(
        f"{BASE}/systems/field_values/system_names",
        cancel,
        params=[("q", text)],
        ttl=NAMES_TTL,
        valid=lambda doc: _names(doc) is not None,
    )
    return _names(_ok_json(resp, "system names"))


def _names(doc: Any) -> tuple[SystemHit, ...]:
    rows = doc.get("min_max") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise BadReply("system names: no 'min_max' list")
    return tuple(_system_hit(row) for row in rows)


def systems_by_name(
    client: PoliteClient, names: list[str], cancel: threading.Event
) -> tuple[SystemInfo, ...]:
    """The systems called exactly one of `names` (letter case matters: send Spansh's own spelling, e.g.
    from the autocomplete), each with its stations and settlements."""
    names = list(dict.fromkeys(n for n in names if n))[:MAX_SEARCH_NAMES]
    if not names:
        return ()
    query = {"filters": {"name": {"value": names}}, "sort": [], "size": len(names) * 2, "page": 0}
    rows = _search(client, "systems", query, cancel)
    return tuple(_system_info(row) for row in rows)


def market_stations(
    client: PoliteClient, system: str, cancel: threading.Event
) -> tuple[StationInfo, ...] | None:
    """The stations Spansh says have a market in the system called exactly `system` (Spansh's spelling),
    nearest to the star first; None when Spansh knows no system spelled so. Its own query (size 4, not the
    gear lookups' 2), so rows cached before stations carried `has_market` are never read here."""
    name = system.strip()
    if not name:
        return None
    query = {"filters": {"name": {"value": [name]}}, "sort": [], "size": 4, "page": 0}
    rows = _search(client, "systems", query, cancel)
    found = [s for s in (_system_info(row) for row in rows) if s.name == name]
    if not found:
        return None
    stations = [st for st in found[0].stations if st.market]
    return tuple(sorted(stations, key=lambda st: (st.arrival_ls is None, st.arrival_ls or 0.0, st.name)))


def stations_by_name(
    client: PoliteClient, names: list[str], cancel: threading.Event
) -> tuple[tuple[StationInfo, SystemInfo], ...]:
    """Stations and settlements called exactly one of `names` (letter case matters: send the spellings
    of ONE name), in any system, each with its system (no station list). Up to 50 rows."""
    names = list(dict.fromkeys(n for n in names if n))[:MAX_SEARCH_NAMES]
    if not names:
        return ()
    query = {"filters": {"name": {"value": names}}, "sort": [], "size": 50, "page": 0}
    out = []
    for row in _search(client, "stations", query, cancel):
        r = _obj(row, "station")
        system = SystemInfo(
            id64=_int(r, "system_id64"),
            name=_str(r, "system_name"),
            x=_num(r, "system_x"),
            y=_num(r, "system_y"),
            z=_num(r, "system_z"),
        )
        out.append((_station(r, system.id64, system.name), system))
    return tuple(out)


def market_form(q: MarketQuery, page: int, size: int = MARKET_PAGE) -> dict[str, Any]:
    """Spansh's station search for one commodity, nearest first (docs/api-notes.md): stock (buy) or demand
    (sell) of at least `min_qty`, market data no older than `max_age_days`; the fleet carriers left out by
    listing every other station type, a large pad asked for when the ship needs one."""
    req = q.request
    amount = "supply" if req.side == "buy" else "demand"
    # The time window moves in MARKET_TTL steps, so the same search again within them is the same request
    # (answered from the cache); the exact age is cut by `core.market.usable`.
    step = int(MARKET_TTL)
    now = datetime.fromtimestamp(q.now.timestamp() // step * step, UTC)
    since = (now - timedelta(days=req.max_age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    filters: dict[str, Any] = {
        "market": [
            {"name": req.commodity, amount: {"value": [max(req.min_qty, 1), MAX_AMOUNT], "comparison": "<=>"}}
        ],
        "market_updated_at": {"value": [since, until], "comparison": "<=>"},
    }
    if not req.carriers:
        filters["type"] = {"value": list(STATION_TYPES)}
    if req.pad == "L":
        filters["has_large_pad"] = {"value": True}
    x, y, z = q.star_pos
    return {
        "filters": filters,
        "sort": [{"distance": {"direction": "asc"}}],
        "reference_coords": {"x": x, "y": y, "z": z},
        "size": size,
        "page": page,
    }


def market_page(
    client: PoliteClient, q: MarketQuery, page: int, cancel: threading.Event
) -> tuple[tuple[MarketRow, ...], int]:
    """One page of the market search: its rows and how many stations match in all."""
    query = market_form(q, page)
    cache_key = "spansh market " + json.dumps(query, sort_keys=True)
    cached = client.cache_get(cache_key, MARKET_TTL)
    doc: Any = None
    if cached is not None:
        try:
            doc = json.loads(cached)
        except ValueError:
            doc = None  # damaged: ask again
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        resp = client.search_json(f"{BASE}/stations/search", query, cancel)
        reply = _ok_json(resp, "market search")
        results = reply.get("results") if isinstance(reply, dict) else None
        if not isinstance(results, list):
            raise BadReply("market search: no 'results' list")
        count = reply.get("count")
        doc = {
            "count": count if isinstance(count, int) else len(results),
            "results": [trim_market_row(r, q.request.commodity) for r in results],
        }
        client.cache_put(cache_key, json.dumps(doc))
    rows = tuple(_market_row(r, q.request.side) for r in doc["results"])
    return rows, int(doc["count"])


def trim_market_row(row: Any, commodity: str) -> Any:
    """Only the fields we read, and of the market only the commodity asked."""
    if not isinstance(row, dict):
        return row
    out = {k: row[k] for k in _MARKET_KEEP if k in row}
    market = row.get("market")
    if isinstance(market, list):
        out["market"] = [m for m in market if isinstance(m, dict) and m.get("commodity") == commodity]
    return out


def _market_station(r: dict[str, Any]) -> dict[str, Any]:
    """The fields of a market or sell row that say where the station is and who may dock there."""
    station = _station(r, _int(r, "system_id64"), _str(r, "system_name"))
    access = r.get("carrier_docking_access")
    faction = r.get("controlling_minor_faction")
    carrier = "Carrier" in station.type
    # a Powerplay Stronghold Carrier is listed as a planetary outpost named so, its "faction" the power
    stronghold = station.name == "Stronghold Carrier"
    return {
        "station": station.name,
        "station_type": station.type,
        "system": station.system_name,
        "system_id64": station.system_id64,
        "x": _num(r, "system_x"),
        "y": _num(r, "system_y"),
        "z": _num(r, "system_z"),
        "arrival_ls": station.arrival_ls,
        "pad": station.pad,
        "carrier": carrier,
        "access": access.lower() if isinstance(access, str) else "",
        # a carrier's "faction" is Spansh's placeholder "FleetCarrier", not a faction you have a standing with
        "faction": faction if isinstance(faction, str) and not (carrier or stronghold) else "",
        "updated": str(r.get("market_updated_at") or ""),
        "stronghold": stronghold,
        "power": faction if stronghold and isinstance(faction, str) else "",
    }


def _market_row(row: Any, side: str) -> MarketRow:
    r = _obj(row, "station")
    market = r.get("market")
    entry = market[0] if isinstance(market, list) and market else None
    if not isinstance(entry, dict):
        raise BadReply("market search: a station without the commodity")
    price, amount = ("buy_price", "supply") if side == "buy" else ("sell_price", "demand")
    return MarketRow(
        **_market_station(r),
        price=int(_num(entry, price, 0)),
        quantity=int(_num(entry, amount, 0)),
    )


def sell_form(q: SellQuery, good: Good, page: int, size: int = MARKET_PAGE) -> dict[str, Any]:
    """Spansh's station search for one commodity you sell (docs/api-notes.md): demand of at least the tonnes
    you carry, within `max_ly` of where you are, market data no older than `max_age_days`, the best sell price
    first; fleet carriers and pads as in the commodity search."""
    req = q.request
    step = int(MARKET_TTL)  # the time window moves in steps, as in `market_form`: the same search is cached
    now = datetime.fromtimestamp(q.now.timestamp() // step * step, UTC)
    since = (now - timedelta(days=req.max_age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    filters: dict[str, Any] = {
        "market": [
            {"name": good.name, "demand": {"value": [max(good.tonnes, 1), MAX_AMOUNT], "comparison": "<=>"}}
        ],
        "market_updated_at": {"value": [since, until], "comparison": "<=>"},
        "distance": {"min": "0", "max": f"{req.max_ly:g}"},
    }
    if not req.carriers:
        filters["type"] = {"value": list(STATION_TYPES)}
    if req.pad == "L":
        filters["has_large_pad"] = {"value": True}
    x, y, z = q.star_pos
    return {
        "filters": filters,
        "sort": [{"market_sell_price": [{"name": good.name, "direction": "desc"}]}],
        "reference_coords": {"x": x, "y": y, "z": z},
        "size": size,
        "page": page,
    }


def sell_page(
    client: PoliteClient, q: SellQuery, good: Good, page: int, cancel: threading.Event
) -> tuple[tuple[SellStation, ...], int]:
    """One page of the sell search for `good`: its stations (with their prices for everything in the hold)
    and how many stations match in all."""
    query = sell_form(q, good, page)
    names = sorted(g.name for g in q.goods)
    cache_key = "spansh sell " + json.dumps([query, names], sort_keys=True)
    cached = client.cache_get(cache_key, MARKET_TTL)
    doc: Any = None
    if cached is not None:
        try:
            doc = json.loads(cached)
        except ValueError:
            doc = None  # damaged: ask again
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        resp = client.search_json(f"{BASE}/stations/search", query, cancel)
        reply = _ok_json(resp, "sell search")
        results = reply.get("results") if isinstance(reply, dict) else None
        if not isinstance(results, list):
            raise BadReply("sell search: no 'results' list")
        count = reply.get("count")
        doc = {
            "count": count if isinstance(count, int) else len(results),
            "results": [trim_sell_row(r, names) for r in results],
        }
        client.cache_put(cache_key, json.dumps(doc))
    rows = tuple(_sell_station(r, names) for r in doc["results"])
    return rows, int(doc["count"])


def trim_sell_row(row: Any, commodities: list[str]) -> Any:
    """Only the fields we read, and of the market only the commodities in the hold."""
    if not isinstance(row, dict):
        return row
    out = {k: row[k] for k in _MARKET_KEEP if k in row}
    market = row.get("market")
    if isinstance(market, list):
        wanted = set(commodities)
        out["market"] = [m for m in market if isinstance(m, dict) and m.get("commodity") in wanted]
    return out


def _sell_station(row: Any, commodities: list[str]) -> SellStation:
    r = _obj(row, "station")
    offers = []
    market = r.get("market")
    for m in market if isinstance(market, list) else []:
        if not isinstance(m, dict) or m.get("commodity") not in commodities:
            continue
        price, demand = int(_num(m, "sell_price", 0)), int(_num(m, "demand", 0))
        if price > 0 and demand > 0:
            offers.append(Offer(str(m["commodity"]), price, demand))
    return SellStation(**_market_station(r), offers=tuple(offers))


STATIONS_TTL = 60 * 60.0  # stations rarely move or change hands: an hour
CARRIER_TYPE = "Drake-Class Carrier"


def stations_form(q: ServiceQuery, page: int, size: int = MARKET_PAGE) -> dict[str, Any]:
    """Spansh's station search with no market condition (docs/api-notes.md): the types asked (carriers and
    surface settlements only when wanted), a large pad asked for when the ship needs one, the services asked
    for (a list of `{"name": …}` means every one of them; `{"value": [...]}` would be any one), and the
    primary economies a trader's or broker's kind needs."""
    req = q.request
    types = [t for t in STATION_TYPES if req.settlements or t not in SETTLEMENT_TYPES]
    if req.carriers:
        types.append(CARRIER_TYPE)
    filters: dict[str, Any] = {"type": {"value": types}}
    if req.pad == "L":
        filters["has_large_pad"] = {"value": True}
    if req.services:
        filters["services"] = [{"name": s} for s in req.services]
    economies = req.economies()
    if economies:
        filters["primary_economy"] = {"value": list(economies)}
    x, y, z = q.star_pos
    return {
        "filters": filters,
        "sort": [{"distance": {"direction": "asc"}}],
        "reference_coords": {"x": x, "y": y, "z": z},
        "size": size,
        "page": page,
    }


def stations_page(
    client: PoliteClient, q: ServiceQuery, page: int, cancel: threading.Event
) -> tuple[tuple[ServiceRow, ...], int]:
    """One page of the station search: its rows and how many stations match in all."""
    query = stations_form(q, page)
    cache_key = "spansh stations 2 " + json.dumps(query, sort_keys=True)  # 2: rows keep primary_economy
    cached = client.cache_get(cache_key, STATIONS_TTL)
    doc: Any = None
    if cached is not None:
        try:
            doc = json.loads(cached)
        except ValueError:
            doc = None  # damaged: ask again
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        resp = client.search_json(f"{BASE}/stations/search", query, cancel)
        reply = _ok_json(resp, "station search")
        results = reply.get("results") if isinstance(reply, dict) else None
        if not isinstance(results, list):
            raise BadReply("station search: no 'results' list")
        count = reply.get("count")
        doc = {
            "count": count if isinstance(count, int) else len(results),
            "results": [trim_station_row(r) for r in results],
        }
        client.cache_put(cache_key, json.dumps(doc))
    return tuple(_station_row(r) for r in doc["results"]), int(doc["count"])


def trim_station_row(row: Any) -> Any:
    """Only the fields we read (a station row carries its whole market, outfitting and shipyard)."""
    if not isinstance(row, dict):
        return row
    return {k: row[k] for k in (*_MARKET_KEEP, "updated_at", "primary_economy") if k in row}


def _station_row(row: Any) -> ServiceRow:
    r = _obj(row, "station")
    station = _station(r, _int(r, "system_id64"), _str(r, "system_name"))
    access = r.get("carrier_docking_access")
    faction = r.get("controlling_minor_faction")
    economy = r.get("primary_economy")
    carrier = "Carrier" in station.type
    stronghold = station.name == "Stronghold Carrier"  # see `_market_row`
    return ServiceRow(
        station=station.name,
        station_type=station.type,
        system=station.system_name,
        system_id64=station.system_id64,
        x=_num(r, "system_x"),
        y=_num(r, "system_y"),
        z=_num(r, "system_z"),
        arrival_ls=station.arrival_ls,
        pad=station.pad,
        economy=economy if isinstance(economy, str) else "",
        updated=str(r.get("updated_at") or ""),
        faction=faction if isinstance(faction, str) and not (carrier or stronghold) else "",
        carrier=carrier,
        access=access.lower() if isinstance(access, str) else "",
        stronghold=stronghold,
        power=faction if stronghold and isinstance(faction, str) else "",
    )


def power_form(q: PowerQuery, size: int = POWER_SHOWN) -> dict[str, Any]:
    """Spansh's system search for one power, nearest first (docs/api-notes.md): the systems it holds
    (`controlling_power`), those another power holds where it is present (`power` + every other power as
    `controlling_power`), or the Unoccupied ones it can acquire (`power` + Unoccupied); the first two
    optionally in one power state."""
    req = q.request
    filters: dict[str, Any]
    if req.mode == "held":
        filters = {"controlling_power": {"value": [req.power]}}
    elif req.mode == "others":
        others = [p for p in POWERS if p != req.power]
        filters = {"power": {"value": [req.power]}, "controlling_power": {"value": others}}
    else:
        filters = {"power": {"value": [req.power]}, "power_state": {"value": ["Unoccupied"]}}
    if req.mode != "acquire":
        filters["power_state"] = {"value": [req.state] if req.state else list(CONTROLLED_STATES)}
    x, y, z = q.star_pos
    return {
        "filters": filters,
        "sort": [{"distance": {"direction": "asc"}}],
        "reference_coords": {"x": x, "y": y, "z": z},
        "size": size,
        "page": 0,
    }


def power_systems(
    client: PoliteClient, q: PowerQuery, cancel: threading.Event, size: int = POWER_SHOWN
) -> PowerFound:
    """The nearest systems for the power, and how many match in all."""
    query = power_form(q, size)
    cache_key = "spansh power " + json.dumps(query, sort_keys=True)
    cached = client.cache_get(cache_key, POWER_TTL)
    doc: Any = None
    if cached is not None:
        try:
            doc = json.loads(cached)
        except ValueError:
            doc = None  # damaged: ask again
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        resp = client.search_json(f"{BASE}/systems/search", query, cancel)
        reply = _ok_json(resp, "power search")
        results = reply.get("results") if isinstance(reply, dict) else None
        if not isinstance(results, list):
            raise BadReply("power search: no 'results' list")
        count = reply.get("count")
        doc = {
            "count": count if isinstance(count, int) else len(results),
            "results": [trim_power_row(r) for r in results],
        }
        client.cache_put(cache_key, json.dumps(doc))
    return PowerFound(tuple(_power_row(r) for r in doc["results"]), int(doc["count"]))


def power_hold(client: PoliteClient, power: str, cancel: threading.Event) -> tuple[tuple[str, int], ...]:
    """How many systems the power holds in each controlled state: one search per state, one row each (only
    the count is read), cached like the card's searches."""
    counts = []
    for state in reversed(CONTROLLED_STATES):  # Stronghold, Fortified, Exploited
        q = PowerQuery(PowerRequest(power, "held", state), "", (0.0, 0.0, 0.0))
        counts.append((state, power_systems(client, q, cancel, size=1).count))
    return tuple(counts)


def powers_held(client: PoliteClient, cancel: threading.Event) -> tuple[tuple[str, int], ...]:
    """How many systems each of the 12 powers holds (any controlled state), most first: one size-1 search per
    power (only the count is read), cached like the card's searches."""
    counts = []
    for power in POWERS:
        q = PowerQuery(PowerRequest(power, "held", ""), "", (0.0, 0.0, 0.0))
        counts.append((power, power_systems(client, q, cancel, size=1).count))
    return tuple(sorted(counts, key=lambda pc: (-pc[1], pc[0])))


_POWER_KEEP = (
    "id64", "name", "x", "y", "z", "controlling_power", "power", "power_state",
    "power_state_control_progress", "power_state_reinforcement", "power_state_undermining", "updated_at",
)  # fmt: skip


def trim_power_row(row: Any) -> Any:
    """Only the fields we read (a system row carries its bodies, stations and factions)."""
    return {k: row[k] for k in _POWER_KEEP if k in row} if isinstance(row, dict) else row


def _power_row(row: Any) -> PowerRow:
    r = _obj(row, "power system")
    powers = r.get("power")
    held = r.get("controlling_power")
    state = r.get("power_state")

    def opt(key: str) -> float | None:
        v = r.get(key)
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

    def merits(key: str) -> int | None:
        v = opt(key)
        return None if v is None else int(v)

    return PowerRow(
        system=_str(r, "name"),
        system_id64=_id64(r),
        x=_num(r, "x"),
        y=_num(r, "y"),
        z=_num(r, "z"),
        held_by=held if isinstance(held, str) else "",
        powers=tuple(p for p in powers if isinstance(p, str)) if isinstance(powers, list) else (),
        state=state if isinstance(state, str) else "",
        progress=opt("power_state_control_progress"),
        reinforcement=merits("power_state_reinforcement"),
        undermining=merits("power_state_undermining"),
        updated=str(r.get("updated_at") or ""),
    )


def _search(client: PoliteClient, what: str, query: dict[str, Any], cancel: threading.Event) -> list[Any]:
    """POST /{what}/search; cached by query (the replies hold whole markets, so they are trimmed first)."""
    cache_key = f"spansh {what} search " + json.dumps(query, sort_keys=True)
    cached = client.cache_get(cache_key, SEARCH_TTL)
    if cached is not None:
        try:
            rows = json.loads(cached)
            if isinstance(rows, list):
                return rows
        except ValueError:
            pass  # damaged: ask again
    resp = client.search_json(f"{BASE}/{what}/search", query, cancel)
    doc = _ok_json(resp, f"{what} search")
    rows = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise BadReply(f"{what} search: no 'results' list")
    rows = [trim_row(row, what) for row in rows]
    client.cache_put(cache_key, json.dumps(rows))
    return rows


_SYSTEM_KEEP = ("id64", "name", "x", "y", "z")
_STATION_KEEP = (
    "name", "type", "distance_to_arrival", "has_large_pad", "medium_pads", "small_pads", "is_planetary",
    "system_id64", "system_name", "system_x", "system_y", "system_z", "has_market",
)  # fmt: skip
_MARKET_KEEP = (
    *_STATION_KEEP, "market_updated_at", "controlling_minor_faction", "carrier_docking_access",
)  # fmt: skip


def trim_row(row: Any, what: str) -> Any:
    """Only the fields we read (a station row carries its whole market, outfitting and shipyard)."""
    if not isinstance(row, dict):
        return row
    if what == "stations":
        return {k: row[k] for k in _STATION_KEEP if k in row}
    out = {k: row[k] for k in _SYSTEM_KEEP if k in row}
    stations = row.get("stations")
    if isinstance(stations, list):
        out["stations"] = [
            {k: st[k] for k in _STATION_KEEP if k in st} for st in stations if isinstance(st, dict)
        ]
    return out


def _system_info(row: Any) -> SystemInfo:
    r = _obj(row, "system")
    id64, name = _id64(r), _str(r, "name")
    stations = r.get("stations") or []
    if not isinstance(stations, list):
        raise BadReply("system: 'stations' is not a list")
    return SystemInfo(
        id64=id64,
        name=name,
        x=_num(r, "x"),
        y=_num(r, "y"),
        z=_num(r, "z"),
        stations=tuple(_station(_obj(st, "station"), id64, name) for st in stations),
    )


def _station(r: dict[str, Any], system_id64: int, system_name: str) -> StationInfo:
    if r.get("has_large_pad") is True:
        pad = "L"
    elif _num(r, "medium_pads", 0) > 0:
        pad = "M"
    elif _num(r, "small_pads", 0) > 0:
        pad = "S"
    else:
        pad = ""
    arrival = r.get("distance_to_arrival")
    return StationInfo(
        name=_str(r, "name"),
        type=str(r.get("type") or ""),
        pad=pad,
        arrival_ls=float(arrival)
        if isinstance(arrival, int | float) and not isinstance(arrival, bool)
        else None,
        planetary=r.get("is_planetary") is True,
        system_id64=system_id64,
        system_name=system_name,
        market=market if isinstance(market := r.get("has_market"), bool) else None,
    )


def scout_form(kind: ScoutKind, q: ScoutQuery) -> list[tuple[str, str]]:
    form = [
        ("from", q.from_system),
        ("to", q.to_system),
        ("range", f"{q.range_ly:.2f}"),
        ("radius", f"{q.radius_ly:g}"),
        ("max_results", str(q.max_results)),
        ("max_distance", str(q.max_distance_ls)),
        ("min_value", str(q.min_value)),
        ("loop", "1" if q.loop else "0"),
        ("avoid_thargoids", "1" if q.avoid_thargoids else "0"),
    ]
    if kind == "riches":
        form.append(("use_mapping_value", "1" if q.use_mapping_value else "0"))
    return form


def scout(
    client: PoliteClient,
    kind: ScoutKind,
    query: ScoutQuery,
    cancel: threading.Event,
    *,
    deadline: float = JOB_DEADLINE,
    clock: Callable[[], float] = time.monotonic,
) -> ScoutResult:
    """Run one tour job to completion (or a cached copy of it) and return the typed result."""
    form = scout_form(kind, query)
    cache_key = f"spansh {kind} " + json.dumps(form)
    cached = client.cache_get(cache_key, RESULT_TTL)
    if cached is not None:
        try:
            return ScoutResult(kind, query, _systems(json.loads(cached)), from_cache=True)
        except (ValueError, BadReply):
            pass  # a stale or damaged entry: fetch afresh

    rows = _run_job(client, JOB_PATHS[kind], form, kind, "search", cancel, deadline, clock)
    if not isinstance(rows, list):
        raise BadReply(f"{kind} result: job did not complete")
    systems = _systems(rows)
    client.cache_put(cache_key, json.dumps(rows))
    return ScoutResult(kind, query, systems)


def resolve_system(client: PoliteClient, name: str, cancel: threading.Event) -> SystemHit:
    """The one system called exactly `name` (any letter case), from Spansh's autocomplete. A name two
    systems share is refused rather than guessed."""
    wanted = name.strip().casefold()
    hits = {h.id64: h for h in system_names(client, name, cancel) if h.name.casefold() == wanted}
    if len(hits) > 1:
        raise SourceError(f"Spansh knows {len(hits)} systems called '{name.strip()}'; cannot tell which one")
    if not hits:
        raise SourceError(f"Spansh does not know a system called '{name.strip()}'")
    return next(iter(hits.values()))


def plot_form(q: PlotQuery) -> list[tuple[str, str]]:
    """Galaxy plotter form, as spansh.co.uk's own exact-plotter page sends it (docs/api-notes.md).
    Neutron supercharges only when asked, no FSD injections, refuel only where needed, Spansh's default
    algorithm."""
    s = q.ship
    return [
        ("source", str(q.source_id64)),
        ("destination", str(q.destination_id64)),
        ("is_supercharged", "0"),
        ("use_supercharge", "1" if q.neutrons else "0"),
        ("use_injections", "0"),
        ("use_injections_when_required", "0"),
        ("exclude_secondary", "0"),
        ("refuel_every_scoopable", "0"),
        ("fuel_power", _dec(s.fuel_power)),
        ("fuel_multiplier", _dec(s.fuel_multiplier)),
        ("optimal_mass", _dec(s.optimal_mass)),
        ("base_mass", _dec(s.base_mass)),
        ("tank_size", _dec(s.tank_size)),
        ("internal_tank_size", _dec(s.reservoir_size)),
        ("reserve_size", "0"),
        ("max_fuel_per_jump", _dec(s.max_fuel_per_jump)),
        ("range_boost", _dec(s.range_boost)),
        ("max_time", "60"),
        ("cargo", str(q.cargo)),
        ("algorithm", "optimistic"),
        ("supercharge_multiplier", str(s.supercharge_multiplier)),
        ("injection_multiplier", "2"),
    ]


def _dec(x: float) -> str:
    """Plain decimal, no exponent, no trailing zeros: 573.549988 → "573.549988", 1175.0 → "1175"."""
    return f"{x:.6f}".rstrip("0").rstrip(".")


def plot(
    client: PoliteClient,
    query: PlotQuery,
    cancel: threading.Event,
    *,
    deadline: float = JOB_DEADLINE,
    clock: Callable[[], float] = time.monotonic,
) -> PlotResult:
    """Run one galaxy-plotter job (or a cached copy of it) and return its waypoints."""
    kind = query.kind
    form = plot_form(query)
    cache_key = "spansh plot " + json.dumps(form)
    cached = client.cache_get(cache_key, RESULT_TTL)
    if cached is not None:
        try:
            return PlotResult(kind, _waypoints(json.loads(cached), query.neutrons), from_cache=True)
        except (ValueError, BadReply):
            pass  # a stale or damaged entry: fetch afresh

    result = _run_job(client, PLOT_PATH, form, "plot", "route", cancel, deadline, clock)
    waypoints = _waypoints(result, query.neutrons)
    client.cache_put(cache_key, json.dumps(result))
    return PlotResult(kind, waypoints)


def trade_form(q: TradeQuery) -> list[tuple[str, str]]:
    """Trade-router form, as spansh.co.uk's own trade page sends it (docs/api-notes.md): one hop from the
    station you start at. Prohibited goods and permit-locked systems stay out."""
    r = q.request
    return [
        ("max_hops", "1"),
        ("max_hop_distance", _dec(r.max_hop_ly)),
        ("system", r.system),
        ("station", r.station),
        ("starting_capital", str(q.capital)),
        ("max_cargo", str(q.cargo)),
        ("max_system_distance", str(r.max_arrival_ls)),
        ("max_price_age", str(q.max_age_seconds)),
        ("requires_large_pad", "1" if r.pad == "L" else "0"),
        ("allow_prohibited", "0"),
        ("allow_planetary", "1" if r.planetary else "0"),
        ("allow_player_owned", "1" if r.carriers else "0"),
        ("unique", "0"),
        ("permit", "0"),
    ]


def trade(
    client: PoliteClient,
    query: TradeQuery,
    cancel: threading.Event,
    *,
    deadline: float = JOB_DEADLINE,
    clock: Callable[[], float] = time.monotonic,
) -> TradeFound:
    """Run one trade-router job (or a copy of it from the last 10 minutes: markets change)."""
    form = trade_form(query)
    cache_key = "spansh trade " + json.dumps(form)
    cached = client.cache_get(cache_key, MARKET_TTL)
    if cached is not None:
        try:
            return _trade(json.loads(cached))
        except (ValueError, BadReply):
            pass  # a stale or damaged entry: fetch afresh
    result = _run_job(client, TRADE_PATH, form, "trade", "search", cancel, deadline, clock)
    found = _trade(result)
    client.cache_put(cache_key, json.dumps(result))
    return found


def _trade(result: Any) -> TradeFound:
    """The trade router's `result`: a list of hops (one asked); empty when nothing pays within the limits."""
    if not isinstance(result, list):
        raise BadReply("trade result: not a list")
    if not result:
        return TradeFound()
    hop = _obj(result[0], "trade hop")
    goods = hop.get("commodities")
    if not isinstance(goods, list) or not goods:
        raise BadReply("trade hop: no commodities")
    return TradeFound(
        source=_trade_station(hop.get("source")),
        destination=_trade_station(hop.get("destination")),
        distance_ly=_num(hop, "distance"),
        goods=tuple(_trade_good(g) for g in goods),
        total_profit=_int(hop, "total_profit"),
    )


def _trade_station(value: Any) -> TradeStation:
    s = _obj(value, "trade station")
    updated = s.get("market_updated_at")
    when = ""
    if isinstance(updated, int | float) and not isinstance(updated, bool) and updated > 0:
        when = datetime.fromtimestamp(updated, UTC).isoformat(timespec="seconds")
    arrival = s.get("distance_to_arrival")
    id64 = s.get("system_id64")
    return TradeStation(
        system=_str(s, "system"),
        system_id64=id64 if isinstance(id64, int) and not isinstance(id64, bool) else None,
        station=_str(s, "station"),
        arrival_ls=float(arrival)
        if isinstance(arrival, int | float) and not isinstance(arrival, bool)
        else None,
        updated=when,
    )


def _trade_good(value: Any) -> TradeGood:
    g = _obj(value, "trade commodity")
    src = _obj(g.get("source_commodity"), "trade commodity source")
    dst = _obj(g.get("destination_commodity"), "trade commodity destination")
    return TradeGood(
        name=_str(g, "name"),
        amount=_int(g, "amount"),
        buy_price=_int(src, "buy_price"),
        sell_price=_int(dst, "sell_price"),
        supply=_int(src, "supply"),
        demand=_int(dst, "demand"),
        profit=_int(g, "profit"),
        total_profit=_int(g, "total_profit"),
    )


def _run_job(
    client: PoliteClient,
    path: str,
    form: list[tuple[str, str]],
    kind: str,
    noun: str,
    cancel: threading.Event,
    deadline: float,
    clock: Callable[[], float],
) -> Any:
    """Submit a job, poll it until Spansh stops answering 202, and return its (unchecked) `result`."""
    resp = client.post_form(f"{BASE}/{path}", form, cancel)
    submitted = _ok_json(resp, f"{kind} submit", ok=(200, 202))
    job = submitted.get("job") if isinstance(submitted, dict) else None
    if not isinstance(job, str) or not job:
        raise BadReply(f"{kind} submit: no job id")

    started = clock()
    while True:
        resp = client.get(f"{BASE}/results/{job}", cancel)
        if resp.status != 202:
            break
        if clock() - started > deadline:
            raise SourceError(f"it did not finish the {kind} {noun} in {deadline / 60:.0f} min")
    doc = _ok_json(resp, f"{kind} result")
    if not isinstance(doc, dict) or doc.get("status") != "ok" or "result" not in doc:
        message = doc.get("error") if isinstance(doc, dict) else None
        if isinstance(message, str) and message:  # Spansh's own words: shown as they are
            raise SourceError(message)
        raise BadReply(f"{kind} result: job did not complete")
    return doc["result"]


# --- parsing ---------------------------------------------------------------------------------------
def _ok_json(resp: Response, what: str, ok: tuple[int, ...] = (200,)) -> Any:
    if resp.status not in ok:
        message = None
        try:
            doc = resp.json()
            message = doc.get("error") if isinstance(doc, dict) else None
        except BadReply:
            pass
        said = message if isinstance(message, str) else ""
        raise HttpError(resp.status, f"{what}: {said or f'HTTP {resp.status}'}", said=said)
    return resp.json()


def _systems(rows: Any) -> tuple[ScoutSystem, ...]:
    if not isinstance(rows, list):
        raise BadReply("result is not a list")
    return tuple(_system(row) for row in rows)


def _system(row: Any) -> ScoutSystem:
    r = _obj(row, "system")
    bodies = r.get("bodies", [])
    if not isinstance(bodies, list):
        raise BadReply("system: 'bodies' is not a list")
    return ScoutSystem(
        id64=_id64(r),
        name=_str(r, "name"),
        jumps=int(_num(r, "jumps")),
        x=_num(r, "x"),
        y=_num(r, "y"),
        z=_num(r, "z"),
        bodies=tuple(_body(b) for b in bodies),
    )


def _body(row: Any) -> ScoutBody:
    b = _obj(row, "body")
    landmarks = b.get("landmarks") or []
    if not isinstance(landmarks, list):
        raise BadReply("body: 'landmarks' is not a list")
    return ScoutBody(
        id64=_id64(b),
        name=_str(b, "name"),
        type=_str(b, "type"),
        subtype=_str(b, "subtype"),
        distance_ls=_num(b, "distance_to_arrival"),
        scan_value=int(_num(b, "estimated_scan_value", 0)),
        mapping_value=int(_num(b, "estimated_mapping_value", 0)),
        terraformable=b.get("is_terraformable") is True,
        species=tuple(_species(s) for s in landmarks),
    )


def _species(row: Any) -> BioSpecies:
    s = _obj(row, "landmark")
    return BioSpecies(
        genus=_str(s, "type"),
        species=_str(s, "subtype"),
        value=int(_num(s, "value")),
        count=int(_num(s, "count", 0)),
    )


def _waypoints(result: Any, neutrons: bool) -> tuple[Waypoint, ...]:
    """Galaxy plotter `result.jumps`: one row per jump; the first row is the start. Spansh reports the
    start and the destination as not scoopable whatever their star (Maia's B star, Brestla's red dwarf), so
    there it is "not known". `has_neutron` is read as "supercharge here" only on a route plotted with
    neutrons (checked there: every mark sits right before a boosted jump); a normal route never has one."""
    rows = result.get("jumps") if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) < 2:
        raise BadReply("no route came back")
    out = []
    for i, row in enumerate(rows):
        r = _obj(row, "jump")
        out.append(
            Waypoint(
                id64=_id64(r),
                name=_str(r, "name"),
                x=_num(r, "x"),
                y=_num(r, "y"),
                z=_num(r, "z"),
                leg_ly=_num(r, "distance"),
                jumps=0 if i == 0 else 1,
                neutron=neutrons and r.get("has_neutron") is True,
                refuel=r.get("must_refuel") is True,
                scoopable=None if i in (0, len(rows) - 1) else r.get("is_scoopable") is True,
            )
        )
    return tuple(out)


def _system_hit(row: Any) -> SystemHit:
    r = _obj(row, "system name")
    return SystemHit(id64=_id64(r), name=_str(r, "name"), x=_num(r, "x"), y=_num(r, "y"), z=_num(r, "z"))


def _obj(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BadReply(f"{what}: not an object")
    return value


def _str(d: dict[str, Any], key: str) -> str:
    value = d.get(key)
    if not isinstance(value, str):
        raise BadReply(f"'{key}' missing or not text")
    return value


def _num(d: dict[str, Any], key: str, default: float | None = None) -> float:
    value = d.get(key)
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BadReply(f"'{key}' missing or not a number")
    return float(value)


def _int(d: dict[str, Any], key: str) -> int:
    """An exact integer that may arrive as a number or a digit string (ids)."""
    value = d.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise BadReply(f"'{key}' missing or not an integer")


def _id64(d: dict[str, Any]) -> int:
    """Spansh sends id64 as a string (and sometimes a number); both become an exact int."""
    value = d.get("id64")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise BadReply("'id64' missing or not an integer")


# --- the bodies of one system (the system you just arrived in) ---------------------------------------

_BODY_KEEP = (
    "body_id", "name", "type", "subtype", "distance_to_arrival", "estimated_scan_value",
    "estimated_mapping_value", "terraforming_state", "is_landable",
)  # fmt: skip
_TERRAFORMABLE = ("Terraformable", "Candidate for terraforming", "Terraforming")


def bodies_query(id64: int, page: int) -> dict[str, Any]:
    return {"filters": {"system_id64": {"value": id64}}, "size": BODIES_PAGE, "page": page}


def system_bodies(client: PoliteClient, id64: int, cancel: threading.Event) -> tuple[SystemBody, ...]:
    """Every body Spansh knows in the system (none: Spansh has no body there). One body search per 100
    bodies, cached for an hour."""
    cache_key = f"spansh bodies {id64}"
    cached = client.cache_get(cache_key, BODIES_TTL)
    if cached is not None:
        try:
            return _system_bodies(json.loads(cached))
        except (ValueError, BadReply):
            pass  # damaged: ask again
    rows: list[Any] = []
    for page in range(MAX_BODY_PAGES):
        resp = client.search_json(f"{BASE}/bodies/search", bodies_query(id64, page), cancel)
        doc = _ok_json(resp, "body search")
        found = doc.get("results") if isinstance(doc, dict) else None
        if not isinstance(found, list):
            raise BadReply("body search: no 'results' list")
        rows += [trim_body_row(r) for r in found]
        count = doc.get("count")
        if len(found) < BODIES_PAGE or not isinstance(count, int) or len(rows) >= count:
            break
    bodies = _system_bodies(rows)
    client.cache_put(cache_key, json.dumps(rows))
    return bodies


def trim_body_row(row: Any) -> Any:
    """Only the fields we read; of the landmarks, the sampled species (a row also lists every geyser,
    settlement and sighting with its coordinates)."""
    if not isinstance(row, dict):
        return row
    out = {k: row[k] for k in _BODY_KEEP if k in row}
    signals = row.get("signals")
    if isinstance(signals, list):
        out["signals"] = [s for s in signals if isinstance(s, dict) and s.get("name") == "Biological"]
    genuses = row.get("genuses")
    if isinstance(genuses, list):
        out["genuses"] = [{"name": g.get("name")} for g in genuses if isinstance(g, dict)]
    landmarks = row.get("landmarks")
    if isinstance(landmarks, list):
        seen: dict[str, dict[str, Any]] = {}
        for m in landmarks:
            if isinstance(m, dict) and isinstance(m.get("value"), int | float) and m["value"] > 0:
                key = str(m.get("subtype"))
                if key in seen:
                    seen[key]["count"] += 1
                else:
                    seen[key] = {
                        "type": m.get("type"),
                        "subtype": m.get("subtype"),
                        "value": m["value"],
                        "count": 1,
                    }
        out["landmarks"] = list(seen.values())
    return out


def _system_bodies(rows: Any) -> tuple[SystemBody, ...]:
    if not isinstance(rows, list):
        raise BadReply("body search: results are not a list")
    return tuple(_system_body(r) for r in rows)


def _system_body(row: Any) -> SystemBody:
    b = _obj(row, "body")
    star = _str(b, "type") == "Star"
    bio = sum(
        int(s.get("count", 0))
        for s in b.get("signals") or []
        if isinstance(s, dict) and isinstance(s.get("count"), int)
    )
    genera = tuple(
        genus_name(g["name"])
        for g in b.get("genuses") or []
        if isinstance(g, dict) and isinstance(g.get("name"), str)
    )
    return SystemBody(
        body_id=_int(b, "body_id"),
        name=_str(b, "name"),
        star=star,
        subtype=b["subtype"] if isinstance(b.get("subtype"), str) else "",
        distance_ls=_num(b, "distance_to_arrival", 0),
        scan_value=int(_num(b, "estimated_scan_value", 0)),
        mapping_value=0 if star else int(_num(b, "estimated_mapping_value", 0)),
        terraformable=b.get("terraforming_state") in _TERRAFORMABLE,
        landable=b.get("is_landable") is True,
        bio_signals=bio,
        genera=genera,
        species=tuple(_species(s) for s in b.get("landmarks") or []),
    )
