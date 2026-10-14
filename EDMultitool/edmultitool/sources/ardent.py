"""Ardent Insight adapter (the market backup when Spansh fails): the only place that knows Ardent's raw
JSON. Request shapes are recorded in docs/api-notes.md.

Ardent's nearby market searches take the current system (its id64) and the commodity's game symbol
("lowtemperaturediamond", from `catalog.commodities`), and leave the current system itself out. They know
no station faction, so a row's standing stays unknown.
"""

import threading
from typing import Any
from urllib.parse import quote

from edmultitool.catalog.commodities import COMMODITIES
from edmultitool.core.market import MarketQuery, MarketRow
from edmultitool.sources.http import BadReply, HttpError, PoliteClient, Response, SourceError

BASE = "https://api.ardent-insight.com/v2"
MAX_DISTANCE_LY = 100  # Ardent's search radius around the current system
MARKET_TTL = 10 * 60.0
PADS = {1: "S", 2: "M", 3: "L"}
_TYPES = {  # Ardent's station types → the names Spansh (and the game) use
    "AsteroidBase": "Asteroid base",
    "Coriolis": "Coriolis Starport",
    "CraterOutpost": "Planetary Outpost",
    "CraterPort": "Planetary Port",
    "Dodec": "Dodec Starport",
    "FleetCarrier": "Drake-Class Carrier",
    "MegaShip": "Mega ship",
    "Ocellus": "Ocellus Starport",
    "OnFootSettlement": "Settlement",
    "Orbis": "Orbis Starport",
    "Outpost": "Outpost",
    "StrongholdCarrier": "Stronghold Carrier",
    "SurfaceStation": "Planetary Port",
}


def nearby_form(q: MarketQuery) -> list[tuple[str, str]]:
    req = q.request
    days = max(1, int(-(-req.max_age_days // 1)))  # whole days, rounded up: ED Multitool cuts the rest
    form = [
        ("maxDistance", str(MAX_DISTANCE_LY)),
        ("minVolume", str(max(req.min_qty, 1))),
        ("maxDaysAgo", str(days)),
        ("sort", "distance"),
    ]
    if not req.carriers:  # "true" would mean ONLY carriers; no parameter = both
        form.append(("fleetCarriers", "false"))
    return form


def nearby(client: PoliteClient, q: MarketQuery, cancel: threading.Event) -> tuple[MarketRow, ...]:
    """Stations within MAX_DISTANCE_LY of the current system that sell (buy) or buy (sell) the commodity."""
    req = q.request
    symbol = COMMODITIES.get(req.commodity, "")
    if not symbol:
        raise SourceError(f"Ardent has no name for {req.commodity}")
    way = "exports" if req.side == "buy" else "imports"
    where = f"address/{q.address}" if q.address is not None else f"name/{quote(q.system, safe='')}"
    url = f"{BASE}/system/{where}/commodity/name/{symbol}/nearby/{way}"

    def rows(doc: Any) -> tuple[MarketRow, ...]:
        return tuple(_row(r, req.side) for r in _list(doc))

    resp = client.get(
        url, cancel, params=nearby_form(q), ttl=MARKET_TTL, valid=lambda doc: rows(doc) is not None
    )
    _check(resp)
    return rows(resp.json())


def _list(doc: Any) -> list[Any]:
    if not isinstance(doc, list):
        raise BadReply("Ardent: the reply is not a list")
    return doc


def _check(resp: Response) -> None:
    status = resp.status
    if status == 200:
        return
    message = None
    try:
        doc = resp.json()
        message = doc.get("message") or doc.get("error") if isinstance(doc, dict) else None
    except BadReply:
        pass
    said = message if isinstance(message, str) else ""
    raise HttpError(status, f"Ardent: {said or f'HTTP {status}'}", said=said)


def _row(row: Any, side: str) -> MarketRow:
    if not isinstance(row, dict):
        raise BadReply("Ardent: a row is not an object")
    kind = str(row.get("stationType") or "")
    pad = row.get("maxLandingPadSize")
    address = row.get("systemAddress")
    arrival = row.get("distanceToArrival")
    price, amount = ("buyPrice", "stock") if side == "buy" else ("sellPrice", "demand")
    return MarketRow(
        station=_str(row, "stationName"),
        station_type=_TYPES.get(kind, kind),
        system=_str(row, "systemName"),
        system_id64=address if isinstance(address, int) and not isinstance(address, bool) else None,
        x=_num(row, "systemX"),
        y=_num(row, "systemY"),
        z=_num(row, "systemZ"),
        arrival_ls=float(arrival)
        if isinstance(arrival, int | float) and not isinstance(arrival, bool)
        else None,
        pad=PADS.get(pad, "") if isinstance(pad, int) else "",
        carrier=kind == "FleetCarrier",
        access="",
        faction="",
        price=int(_num(row, price, 0)),
        quantity=int(_num(row, amount, 0)),
        updated=_iso(row.get("updatedAt")),
        stronghold=kind == "StrongholdCarrier",  # its power is not in Ardent's reply
    )


def _iso(value: Any) -> str:
    """ "2026-07-20T06:47:55.000Z" → "2026-07-20T06:47:55Z" (the page and Spansh's spelling)."""
    if not isinstance(value, str):
        return ""
    head, dot, _rest = value.partition(".")
    return head + "Z" if dot and value.endswith("Z") else value


def _str(d: dict[str, Any], key: str) -> str:
    value = d.get(key)
    if not isinstance(value, str):
        raise BadReply(f"Ardent: '{key}' missing or not text")
    return value


def _num(d: dict[str, Any], key: str, default: float | None = None) -> float:
    value = d.get(key)
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BadReply(f"Ardent: '{key}' missing or not a number")
    return float(value)
