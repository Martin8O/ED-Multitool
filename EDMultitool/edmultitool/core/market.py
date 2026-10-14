"""Nearest place to buy or sell a commodity — pure: the request the page sends, the typed market rows
the sources return, the filters ED Multitool applies itself, and what the page's "Commodity" card shows
(`UiState.market`).

Spansh filters the market (commodity, stock or demand, data age) and, when asked, leaves the fleet
carriers out; ED Multitool itself checks the pad, the carriers' docking access, your standing with the
station's faction, and keeps the nearest `count`. Distances are measured again from wherever you are on
every refresh; the jump count is the fewest a straight leg can take with the routing range.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol

from edmultitool.catalog.commodities import COMMODITIES
from edmultitool.core.events import StarPos
from edmultitool.core.route import min_jumps

Side = Literal["buy", "sell"]
Pad = Literal["S", "M", "L"]
MarketStatus = Literal["idle", "running", "done", "error"]
Standing = Literal["", "hostile", "unfriendly", "neutral", "cordial", "friendly", "allied"]

PAD_RANK = {"S": 1, "M": 2, "L": 3}
COUNT = 10  # rows shown
MAX_QTY = 1_000_000_000
MAX_AGE_DAYS = 30.0

# Your reputation with a minor faction (journal `MyReputation`, -100..100) → the game's standing: a value
# above each band's floor (Frontier's journal manual, the same cut EDDiscovery makes). A station whose
# controlling faction holds you Hostile refuses docking.
STANDINGS: tuple[tuple[float, Standing], ...] = (
    (90.0, "allied"),
    (35.0, "friendly"),
    (4.0, "cordial"),
    (-35.0, "neutral"),
)
HOSTILE_BELOW = -90.0

_BY_KEY = {name.casefold(): name for name in COMMODITIES}


def commodity_name(text: str) -> str | None:
    """The catalog's own spelling of a commodity typed in any letter case; None when it is not one."""
    return _BY_KEY.get(" ".join(text.split()).casefold())


def standing(reputation: float | None) -> Standing:
    """The game's standing; empty when the faction is not known (never met, or the station has none)."""
    if reputation is None:
        return ""
    for floor, name in STANDINGS:
        if reputation > floor:
            return name
    return "unfriendly" if reputation >= HOSTILE_BELOW else "hostile"


@dataclass(frozen=True, slots=True)
class MarketRequest:
    """What the page asks for (POST /api/market)."""

    commodity: str  # the catalog's spelling
    side: Side  # "buy" = where it is sold to you (stock), "sell" = where it is bought from you (demand)
    min_qty: int = 10  # least stock / demand in t (small amounts matter)
    pad: Pad = "L"  # the pad your ship needs
    carriers: bool = False  # include fleet carriers (their prices are often far off)
    max_age_days: float = 3.0  # oldest market data still shown
    # leave out stations that would refuse you docking: their faction holds you Hostile, another power's
    # Stronghold Carrier, a carrier closed to visitors
    hide_barred: bool = True


@dataclass(frozen=True, slots=True)
class MarketQuery:
    """A request with the place it is measured from (the worker's input)."""

    request: MarketRequest
    system: str  # the system you are in
    address: int | None  # its id64 (Ardent's reference)
    star_pos: StarPos
    now: datetime  # UTC; data older than `request.max_age_days` before it is left out
    count: int = COUNT

    @property
    def oldest(self) -> datetime:
        return self.now - timedelta(days=self.request.max_age_days)


@dataclass(frozen=True, slots=True)
class MarketRow:
    """One station's market for the commodity asked, as a source reports it."""

    station: str
    station_type: str  # "Coriolis Starport", "Drake-Class Carrier", …
    system: str
    system_id64: int | None
    x: float
    y: float
    z: float
    arrival_ls: float | None
    pad: str  # largest pad: "L" / "M" / "S" / "" (none known)
    carrier: bool
    access: str  # a carrier's docking access, lower case ("all", "none", "friends", …); "" = not known
    faction: str  # controlling minor faction; "" = not known
    price: int  # Cr/t: what you pay (buy) or get (sell)
    quantity: int  # t: stock (buy) or demand (sell)
    updated: str  # ISO time of the market data
    stronghold: bool = False  # a Powerplay Stronghold Carrier: only pilots pledged to its power may dock
    power: str = ""  # a Stronghold Carrier's power ("" = not known)


class Dockable(Protocol):
    """What decides whether a station lets you dock: a market row or a station row."""

    @property
    def carrier(self) -> bool: ...
    @property
    def access(self) -> str: ...
    @property
    def stronghold(self) -> bool: ...
    @property
    def power(self) -> str: ...
    @property
    def faction(self) -> str: ...


@dataclass(frozen=True, slots=True)
class Standings:
    """Who you are to the stations: your reputation per minor faction (casefolded name → -100..100) and the
    power you are pledged to ("" = none)."""

    reputation: Mapping[str, float] = field(default_factory=dict)
    power: str = ""

    def of(self, faction: str) -> Standing:
        return standing(self.reputation.get(faction.casefold())) if faction else ""

    def barred(self, r: Dockable) -> bool:
        """The station would refuse you docking: a carrier its owner keeps closed to visitors, another
        power's Stronghold Carrier (or one whose power is not known), or its faction holds you Hostile."""
        if r.carrier:
            return r.access not in ("all", "")  # "" = not known (Ardent never says)
        if r.stronghold:
            return not (r.power and self.power and r.power.casefold() == self.power.casefold())
        return self.of(r.faction) == "hostile"


@dataclass(frozen=True, slots=True)
class MarketFound:
    """What a source found (the worker's answer): every row that passes the filters that do not depend on
    who you are, nearest first. Who may dock is decided on the main thread with the standings known then."""

    rows: tuple[MarketRow, ...]
    source: str  # "Spansh" / "Ardent"
    note: str = ""  # why the backup answered (Spansh's failure)


@dataclass(frozen=True, slots=True)
class MarketResult:
    rows: tuple[MarketRow, ...]  # the nearest `count` that pass every filter, nearest first
    source: str  # "Spansh" / "Ardent"
    barred: int = 0  # stations left out because they would refuse you docking
    note: str = ""  # why the backup answered (Spansh's failure)


def _time(iso: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def dist(a: StarPos, b: StarPos) -> float:
    return math.dist(a, b)


def usable(rows: Iterable[MarketRow], query: MarketQuery) -> list[MarketRow]:
    """Rows that pass ED Multitool's own filters except who may dock, each station once, nearest first."""
    req = query.request
    need = PAD_RANK[req.pad]
    least = max(req.min_qty, 1)  # a station with no stock / no demand is never a place to trade
    out: list[MarketRow] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r.system.casefold(), r.station.casefold())
        if key in seen:
            continue
        seen.add(key)
        if r.price <= 0 or r.quantity < least or PAD_RANK.get(r.pad, 0) < need:
            continue
        if r.carrier and not req.carriers:
            continue
        t = _time(r.updated)
        if t is None or t < query.oldest:
            continue
        out.append(r)
    out.sort(key=lambda r: dist(query.star_pos, (r.x, r.y, r.z)))
    return out


def split(
    rows: list[MarketRow], req: MarketRequest, you: Standings
) -> tuple[list[MarketRow], list[MarketRow]]:
    """The rows shown and those left out because they would refuse you docking (`hide_barred`)."""
    if not req.hide_barred:
        return list(rows), []
    return [r for r in rows if not you.barred(r)], [r for r in rows if you.barred(r)]


def keep(
    rows: Iterable[MarketRow], query: MarketQuery, you: Standings
) -> tuple[list[MarketRow], list[MarketRow]]:
    """Rows that pass every filter, nearest first (all of them), and those left out only because they
    would refuse you docking."""
    return split(usable(rows, query), query.request, you)


def nearest(
    query: MarketQuery, kept: list[MarketRow], barred: list[MarketRow], source: str, note: str = ""
) -> MarketResult:
    """The nearest `count` kept rows; the barred ones counted only up to the farthest row shown (all of
    them when fewer than `count` are shown)."""
    rows = kept[: query.count]
    if len(rows) < query.count:
        hidden = len(barred)
    else:
        reach = dist(query.star_pos, (rows[-1].x, rows[-1].y, rows[-1].z))
        hidden = sum(1 for r in barred if dist(query.star_pos, (r.x, r.y, r.z)) <= reach)
    return MarketResult(tuple(rows), source, hidden, note)


# --- what the page shows ------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class UiMarketRow:
    system: str
    system_id64: str  # decimal string ("" when the source gave none)
    station: str
    station_type: str
    pad: str
    arrival_ls: float | None
    carrier: bool
    faction: str
    standing: Standing  # your standing with `faction` ("" = not known)
    barred: bool  # it would refuse you docking (shown only with hide_barred off)
    power: str  # a Stronghold Carrier's power ("" = not one, or not known)
    price: int
    quantity: int
    updated: str  # ISO time of the market data (the page colours its age)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    distance_ly: float | None = None  # from where you are now, 2 decimals
    jumps: int | None = None  # the fewest jumps a straight leg can take with the routing range


@dataclass(frozen=True, slots=True)
class UiMarket:
    status: MarketStatus = "idle"
    message: str = ""
    commodity: str = ""
    side: Side = "buy"
    rows: tuple[UiMarketRow, ...] = ()
    source: str = ""  # "Spansh" / "Ardent"
    searched: str = ""  # ISO time of the search
    searched_from: str = ""  # the system it was measured from
    barred: int = 0  # stations left out because they would refuse you docking
    note: str = ""  # why the backup answered
    handled: int = 0  # market requests carried out: the page knows its click was answered


def ui_rows(result: MarketResult, you: Standings) -> tuple[UiMarketRow, ...]:
    return tuple(
        UiMarketRow(
            system=r.system,
            system_id64="" if r.system_id64 is None else str(r.system_id64),
            station=r.station,
            station_type=r.station_type,
            pad=r.pad,
            arrival_ls=None if r.arrival_ls is None else round(r.arrival_ls),
            carrier=r.carrier,
            faction=r.faction,
            standing=you.of(r.faction),
            barred=you.barred(r),
            power=r.power,
            price=r.price,
            quantity=r.quantity,
            updated=r.updated,
            x=r.x,
            y=r.y,
            z=r.z,
        )
        for r in result.rows
    )


def with_distances(market: UiMarket, star_pos: StarPos | None, range_ly: float | None) -> UiMarket:
    """Distance and fewest jumps from where you are now to every row (None when not known)."""
    if not market.rows:
        return market
    rows = []
    for r in market.rows:
        d = None if star_pos is None else round(dist(star_pos, (r.x, r.y, r.z)), 2)
        jumps = min_jumps(d, range_ly) if d is not None and range_ly is not None and range_ly > 0 else None
        rows.append(replace(r, distance_ly=d, jumps=jumps))
    out = tuple(rows)
    return market if out == market.rows else replace(market, rows=out)


def parse_market_request(raw: object) -> MarketRequest | str:
    """POST /api/market: {commodity, side, min_qty, pad, carriers, max_age_days, hide_barred}; a message
    when it is not one."""
    if not isinstance(raw, dict):
        return "send a JSON object"
    name = raw.get("commodity")
    commodity = commodity_name(name) if isinstance(name, str) else None
    if commodity is None:
        return "pick a commodity from the suggestions"
    side, pad = raw.get("side"), raw.get("pad")
    if side not in ("buy", "sell"):
        return "side must be buy or sell"
    if pad not in PAD_RANK:
        return "pad must be S, M or L"
    qty, age = raw.get("min_qty"), raw.get("max_age_days")
    if type(qty) is not int or not 0 <= qty <= MAX_QTY:
        return "least amount must be a whole number of tonnes"
    if isinstance(age, bool) or not isinstance(age, int | float) or not 0 < age <= MAX_AGE_DAYS:
        return f"data age must be more than 0 and at most {MAX_AGE_DAYS:g} days"
    flags = [raw.get("carriers"), raw.get("hide_barred")]
    if not all(isinstance(f, bool) for f in flags):
        return "carriers and hide_barred must be true or false"
    return MarketRequest(
        commodity=commodity,
        side=side,
        min_qty=qty,
        pad=pad,
        carriers=raw["carriers"],
        max_age_days=float(age),
        hide_barred=raw["hide_barred"],
    )
