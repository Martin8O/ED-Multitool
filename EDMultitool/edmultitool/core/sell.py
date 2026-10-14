"""Where to sell what you carry — pure: the hold as the game lists it, the request the page sends, the
stations a source reports with their prices for everything you carry, and what the "Sell cargo" card shows
(`UiState.sell`).

Each commodity in the hold is one search: the stations within `max_ly` that buy at least the tonnes you carry,
best price first. Every station a source returns carries its prices for the whole hold, so one stop for the
load is picked from all the stations the searches found (the best payers of each commodity), never a station
no search returned: the best-paying one that buys all of it, else the one that pays most for part. Mission
cargo, stolen tonnes and limpets are not for sale here; what you mined this session comes from
`Earnings.refined`.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal

from edmultitool.catalog.commodities import COMMODITIES
from edmultitool.core.events import CargoHeld, Refined, StarPos
from edmultitool.core.market import MAX_AGE_DAYS, PAD_RANK, Pad, Standing, Standings, commodity_name, dist
from edmultitool.core.route import min_jumps

SellStatus = Literal["idle", "running", "done", "error"]

SHOWN = 3  # offers shown per commodity
MAX_GOODS = 12  # commodities searched per click (one search each)
MAX_LY = 500.0
MIN_HOURS = 0.1  # a mining rate over less than 6 minutes says nothing
LIMPETS = "drones"  # the journal's symbol for limpets: bought, not sold

_BY_SYMBOL = {symbol: name for name, symbol in COMMODITIES.items() if symbol}


def catalog_name(symbol: str, local: str = "") -> str | None:
    """The catalog's spelling of a commodity the journal names by symbol (and display name); None when the
    catalog does not know it."""
    return _BY_SYMBOL.get(symbol.strip().lower()) or (commodity_name(local) if local else None)


@dataclass(frozen=True, slots=True)
class Good:
    name: str  # the catalog's spelling (Spansh's market filter)
    tonnes: int  # legal, your own: what you can sell at a market


@dataclass(frozen=True, slots=True)
class UiHoldLine:
    name: str  # the catalog's spelling, else the game's
    tonnes: int
    note: str = ""  # why some or all of it is not searched ("" = all of it is)


def hold(held: CargoHeld | None) -> tuple[tuple[Good, ...], tuple[UiHoldLine, ...]]:
    """The goods to search (one per commodity, largest load first, at most MAX_GOODS) and every line of the
    hold as shown."""
    if held is None:
        return (), ()
    tonnes: dict[str, int] = {}
    lines: list[UiHoldLine] = []
    for item in held.items:
        name = catalog_name(item.symbol, item.name)
        shown = name or item.name or item.symbol
        if item.symbol == LIMPETS:
            lines.append(UiHoldLine("Limpets", item.count, "limpets: not for sale here"))
            continue
        if item.mission:
            lines.append(UiHoldLine(shown, item.count, "mission cargo: deliver it"))
            continue
        if name is None:
            lines.append(UiHoldLine(shown, item.count, "no market data for this"))
            continue
        legal = item.count - item.stolen
        note = f"{item.stolen} t stolen: only a black market buys that" if item.stolen else ""
        lines.append(UiHoldLine(shown, item.count, note))
        if legal > 0:
            tonnes[name] = tonnes.get(name, 0) + legal
    goods = sorted((Good(n, t) for n, t in tonnes.items()), key=lambda g: (-g.tonnes, g.name))
    cut = {g.name for g in goods[MAX_GOODS:]}
    if cut:
        note = f"not searched: {MAX_GOODS} commodities at most, the largest loads first"
        lines = [
            replace(line, note=f"{line.note}; {note}" if line.note else note) if line.name in cut else line
            for line in lines
        ]
    lines.sort(key=lambda line: (bool(line.note), -line.tonnes, line.name))
    return tuple(goods[:MAX_GOODS]), tuple(lines)


@dataclass(frozen=True, slots=True)
class SellRequest:
    """What the page asks for (POST /api/sell)."""

    max_ly: float = 50.0  # the farthest station, straight from where you are
    pad: Pad = "L"  # the pad your ship needs
    carriers: bool = False  # include fleet carriers (their prices are often far off)
    max_age_days: float = 3.0  # oldest market data still used
    hide_barred: bool = True  # leave out stations that would refuse you docking


@dataclass(frozen=True, slots=True)
class SellQuery:
    """A request with the hold and the place it is measured from (the worker's input)."""

    request: SellRequest
    goods: tuple[Good, ...]
    system: str
    star_pos: StarPos
    now: datetime  # UTC

    @property
    def oldest(self) -> datetime:
        return self.now - timedelta(days=self.request.max_age_days)


@dataclass(frozen=True, slots=True)
class Offer:
    commodity: str  # the catalog's spelling
    price: int  # Cr/t the station pays
    demand: int  # t it buys


@dataclass(frozen=True, slots=True)
class SellStation:
    """One station as a source reports it, with what it pays for each commodity you carry (those it buys)."""

    station: str
    station_type: str
    system: str
    system_id64: int | None
    x: float
    y: float
    z: float
    arrival_ls: float | None
    pad: str  # "L" / "M" / "S" / "" (none known)
    carrier: bool
    access: str  # a carrier's docking access, lower case; "" = not known
    faction: str  # controlling minor faction; "" = not known
    updated: str  # ISO time of the market data
    offers: tuple[Offer, ...]
    stronghold: bool = False
    power: str = ""

    def offer(self, commodity: str) -> Offer | None:
        return next((o for o in self.offers if o.commodity == commodity), None)


@dataclass(frozen=True, slots=True)
class SellFound:
    """What the source found (the worker's answer): per commodity of `goods`, the stations that pass every
    filter that does not depend on who you are, best price first."""

    by_good: tuple[tuple[str, tuple[SellStation, ...]], ...]


def usable(rows: Iterable[SellStation], query: SellQuery, good: Good) -> list[SellStation]:
    """Stations that buy all of `good` within reach, each once, best price first (then nearest)."""
    req = query.request
    need = PAD_RANK[req.pad]
    out: list[SellStation] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r.system.casefold(), r.station.casefold())
        if key in seen:
            continue
        seen.add(key)
        o = r.offer(good.name)
        if o is None or o.price <= 0 or o.demand < max(good.tonnes, 1) or PAD_RANK.get(r.pad, 0) < need:
            continue
        if r.carrier and not req.carriers:
            continue
        if dist(query.star_pos, (r.x, r.y, r.z)) > req.max_ly:
            continue
        t = _parse(r.updated)
        if t is None or t < query.oldest:
            continue
        out.append(r)
    out.sort(key=lambda r: (-_price(r, good.name), dist(query.star_pos, (r.x, r.y, r.z))))
    return out


def _price(r: SellStation, commodity: str) -> int:
    o = r.offer(commodity)
    return 0 if o is None else o.price


# --- what the page shows ------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class UiSellPlace:
    system: str
    system_id64: str  # decimal string ("" when the source gave none)
    station: str
    station_type: str
    pad: str
    arrival_ls: float | None
    carrier: bool
    faction: str
    standing: Standing
    barred: bool  # it would refuse you docking (shown only with hide_barred off)
    power: str
    updated: str  # ISO time of the market data
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    distance_ly: float | None = None  # from where you are now
    jumps: int | None = None  # the fewest jumps a straight leg can take with the routing range


@dataclass(frozen=True, slots=True)
class UiSellOffer:
    place: UiSellPlace
    price: int  # Cr/t
    demand: int  # t
    value: int  # Cr for your whole load of it


@dataclass(frozen=True, slots=True)
class UiSellGood:
    name: str
    tonnes: int
    mined: bool  # refined this session
    offers: tuple[UiSellOffer, ...]  # best price first, at most SHOWN
    barred: int  # stations found but left out: they would refuse you docking


@dataclass(frozen=True, slots=True)
class UiSellLine:
    name: str
    tonnes: int
    price: int
    value: int


@dataclass(frozen=True, slots=True)
class UiSellBest:
    """One stop for the searched load: the best-paying station that buys all of it, else the one that pays
    most for part of it (`left` = what it does not buy all of)."""

    place: UiSellPlace
    lines: tuple[UiSellLine, ...]  # what it buys of your load, biggest value first
    total: int  # Cr
    left: tuple[str, ...] = ()  # commodities it does not buy all of: sell them elsewhere
    # a station that buys all of it was found, but this one pays more for part of it (the rest sold elsewhere)
    richer: "UiSellBest | None" = None


@dataclass(frozen=True, slots=True)
class UiRefined:
    """What you mined (refined) this session."""

    tonnes: int = 0
    by: tuple[tuple[str, int], ...] = ()  # (commodity, t), most first
    per_hour: float | None = None  # t per hour from the first tonne to now; None = too short
    since: str = ""  # ISO time of the first tonne this session


@dataclass(frozen=True, slots=True)
class UiSell:
    status: SellStatus = "idle"
    message: str = ""
    hold: tuple[UiHoldLine, ...] = ()  # the hold now (live)
    hold_rough: bool = False  # known only from EDMC's summary: mission and stolen cargo counted as yours
    searched_load: tuple[tuple[str, int], ...] = ()  # (commodity, t) the last search was for
    changed: bool = False  # the hold is not what the last search was for
    goods: tuple[UiSellGood, ...] = ()
    best: UiSellBest | None = None
    max_ly: float = 0.0
    searched: str = ""  # ISO time of the search
    searched_from: str = ""
    refined: UiRefined = field(default_factory=UiRefined)
    handled: int = 0  # sell requests carried out: the page knows its click was answered


def _place(r: SellStation, you: Standings) -> UiSellPlace:
    return UiSellPlace(
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
        updated=r.updated,
        x=r.x,
        y=r.y,
        z=r.z,
    )


def _takes(r: SellStation, good: Good) -> Offer | None:
    o = r.offer(good.name)
    return o if o is not None and o.price > 0 and o.demand >= good.tonnes else None


def best_station(
    stations: Iterable[SellStation], goods: tuple[Good, ...], star_pos: StarPos, whole_first: bool = True
) -> tuple[SellStation, int] | None:
    """One stop for the load: a station that buys all of it first (the best-paying of them), else the one
    that pays most for what it does buy; the nearer of two that pay the same. `whole_first` False: the one
    that pays most, whether or not it buys all of it."""
    best: tuple[bool, int, float, SellStation] | None = None
    for r in stations:
        taken = [o.price * g.tonnes for g in goods if (o := _takes(r, g)) is not None]
        total = sum(taken)
        if total <= 0:
            continue
        whole = len(taken) == len(goods) or not whole_first
        d = dist(star_pos, (r.x, r.y, r.z))
        if best is None or (whole, total, -d) > (best[0], best[1], -best[2]):
            best = (whole, total, d, r)
    return None if best is None else (best[3], best[1])


def sell_result(
    found: SellFound, query: SellQuery, you: Standings, mined: frozenset[str] = frozenset()
) -> tuple[tuple[UiSellGood, ...], UiSellBest | None]:
    """Per commodity its best offers (who may dock decided with `you`), and one stop for it all — never a
    station that would refuse you docking, even when those are shown among the offers."""
    rows = dict(found.by_good)
    goods_out: list[UiSellGood] = []
    pool: dict[tuple[str, str], SellStation] = {}
    for g in query.goods:
        kept = list(rows.get(g.name, ()))
        for r in kept:
            if not you.barred(r):
                pool.setdefault((r.system.casefold(), r.station.casefold()), r)
        barred = [r for r in kept if you.barred(r)] if query.request.hide_barred else []
        if barred:
            kept = [r for r in kept if not you.barred(r)]
        offers = []
        for r in kept[:SHOWN]:
            o = r.offer(g.name)
            if o is not None:
                offers.append(UiSellOffer(_place(r, you), o.price, o.demand, o.price * g.tonnes))
        goods_out.append(UiSellGood(g.name, g.tonnes, g.name in mined, tuple(offers), len(barred)))
    goods_out.sort(key=lambda g: (-(g.offers[0].value if g.offers else -1), g.name))
    picked = best_station(pool.values(), query.goods, query.star_pos)
    if picked is None:
        return tuple(goods_out), None
    best = _stop(*picked, query.goods, you)
    richest = best_station(pool.values(), query.goods, query.star_pos, whole_first=False)
    if not best.left and richest is not None and richest[1] > best.total:
        best = replace(best, richer=_stop(*richest, query.goods, you))
    return tuple(goods_out), best


def _stop(r: SellStation, total: int, goods: tuple[Good, ...], you: Standings) -> UiSellBest:
    lines = [UiSellLine(g.name, g.tonnes, o.price, o.price * g.tonnes) for g in goods if (o := _takes(r, g))]
    lines.sort(key=lambda line: (-line.value, line.name))
    left = tuple(g.name for g in goods if _takes(r, g) is None)
    return UiSellBest(_place(r, you), tuple(lines), total, left)


def _parse(iso: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def ui_refined(refined: Iterable[Refined], session_start: str, latest: str) -> UiRefined:
    """The tonnes refined since the game was last started; the rate runs from the first of them to `latest`
    (the newest journal entry)."""
    if not session_start:
        return UiRefined()
    mine = [r for r in refined if r.when >= session_start]
    if not mine:
        return UiRefined()
    counts: dict[str, int] = {}
    for r in mine:
        name = catalog_name(r.symbol, r.name) or r.name or r.symbol
        counts[name] = counts.get(name, 0) + 1
    by = tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    first, last = _parse(mine[0].when), _parse(max(latest, mine[-1].when))
    hours = (last - first).total_seconds() / 3600 if first and last else 0.0
    per_hour = round(len(mine) / hours, 1) if hours >= MIN_HOURS else None
    return UiRefined(len(mine), by, per_hour, mine[0].when)


def mined_names(refined: UiRefined) -> frozenset[str]:
    return frozenset(name for name, _ in refined.by)


def _with_place(p: UiSellPlace, star_pos: StarPos | None, range_ly: float | None) -> UiSellPlace:
    d = None if star_pos is None else round(dist(star_pos, (p.x, p.y, p.z)), 2)
    jumps = min_jumps(d, range_ly) if d is not None and range_ly is not None and range_ly > 0 else None
    return p if (d, jumps) == (p.distance_ly, p.jumps) else replace(p, distance_ly=d, jumps=jumps)


def with_distances(sell: UiSell, star_pos: StarPos | None, range_ly: float | None) -> UiSell:
    """Distance and fewest jumps from where you are now to every station shown."""
    if not sell.goods and sell.best is None:
        return sell
    goods = tuple(
        replace(g, offers=tuple(replace(o, place=_with_place(o.place, star_pos, range_ly)) for o in g.offers))
        for g in sell.goods
    )
    best = sell.best
    if best is not None:
        richer = best.richer
        if richer is not None:
            richer = replace(richer, place=_with_place(richer.place, star_pos, range_ly))
        best = replace(best, place=_with_place(best.place, star_pos, range_ly), richer=richer)
    return replace(sell, goods=goods, best=best)


def load_of(goods: Iterable[Good]) -> tuple[tuple[str, int], ...]:
    return tuple((g.name, g.tonnes) for g in goods)


def parse_sell_request(raw: object) -> SellRequest | str:
    """POST /api/sell: {max_ly, pad, carriers, max_age_days, hide_barred}; a message when it is not one."""
    if not isinstance(raw, dict):
        return "send a JSON object"
    ly, pad, age = raw.get("max_ly"), raw.get("pad"), raw.get("max_age_days")
    if isinstance(ly, bool) or not isinstance(ly, int | float) or not 0 < ly <= MAX_LY:
        return f"“Sell within” must be more than 0 and at most {MAX_LY:g} ly"
    if pad not in PAD_RANK:
        return "pad must be S, M or L"
    if isinstance(age, bool) or not isinstance(age, int | float) or not 0 < age <= MAX_AGE_DAYS:
        return f"data age must be more than 0 and at most {MAX_AGE_DAYS:g} days"
    flags = [raw.get("carriers"), raw.get("hide_barred")]
    if not all(isinstance(f, bool) for f in flags):
        return "carriers and hide_barred must be true or false"
    return SellRequest(
        max_ly=float(ly),
        pad=pad,
        carriers=raw["carriers"],
        max_age_days=float(age),
        hide_barred=raw["hide_barred"],
    )
