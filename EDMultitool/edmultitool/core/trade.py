"""Best trade from a station — pure: the request the page sends, the trade Spansh's trade router finds
(one hop: what to buy at the station you start from and where to sell it), and what the page's "Best trade"
card shows (`UiState.trade`).

Spansh picks the commodity and the station that buys it for the most profit within the hop distance, filling
your hold as far as your credits and the station's stock allow (it may add a second commodity). ED Multitool
adds the jumps the hop takes with that load aboard; Spansh reports no pad sizes, factions or docking access.
"""

from dataclasses import dataclass
from typing import Literal

from edmultitool.core.market import MAX_AGE_DAYS, PAD_RANK, Pad

TradeStatus = Literal["idle", "running", "done", "error"]

MAX_CARGO = 10_000  # t: no ship holds more (a fleet carrier is not a ship you trade with here)
MAX_CAPITAL = 1_000_000_000_000  # Cr
MAX_HOP_LY = 1_000.0
MAX_ARRIVAL_LS = 1_000_000
MAX_NAME = 100


@dataclass(frozen=True, slots=True)
class TradeRequest:
    """What the page asks for (POST /api/trade); `cargo` / `capital` None = the ship's hold / your balance."""

    system: str  # where you buy
    station: str
    cargo: int | None = None  # t to fill
    capital: int | None = None  # Cr to spend
    max_hop_ly: float = 30.0  # farthest the station you sell at may be
    max_arrival_ls: int = 5000  # farthest from its star a station may be
    max_age_days: float = 1.0  # oldest market data used
    pad: Pad = "L"  # the pad your ship needs (Spansh only tells large pads from the rest)
    carriers: bool = False  # sell to or buy from fleet carriers
    planetary: bool = True  # planetary ports and surface settlements


@dataclass(frozen=True, slots=True)
class TradeQuery:
    """A request with the hold and credits filled in (the worker's input)."""

    request: TradeRequest
    cargo: int
    capital: int

    @property
    def max_age_seconds(self) -> int:
        return round(self.request.max_age_days * 86400)


@dataclass(frozen=True, slots=True)
class TradeStation:
    system: str
    system_id64: int | None
    station: str
    arrival_ls: float | None
    updated: str  # ISO time of its market data ("" = not known)


@dataclass(frozen=True, slots=True)
class TradeGood:
    """One commodity of the trade: what you buy, how much, and what each tonne earns."""

    name: str
    amount: int  # t bought
    buy_price: int  # Cr/t at the start station
    sell_price: int  # Cr/t where you sell
    supply: int  # t the start station has
    demand: int  # t the station you sell at wants
    profit: int  # Cr/t
    total_profit: int  # Cr for `amount`


@dataclass(frozen=True, slots=True)
class TradeFound:
    """Spansh's answer (the worker's): the trade, or None when nothing pays within the limits."""

    source: TradeStation | None = None
    destination: TradeStation | None = None
    distance_ly: float = 0.0  # straight from the start system to the one you sell in
    goods: tuple[TradeGood, ...] = ()
    total_profit: int = 0


# --- what the page shows ------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class UiTradeStation:
    system: str
    system_id64: str  # decimal string ("" when Spansh gave none)
    station: str
    arrival_ls: float | None
    updated: str  # ISO time of its market data (the page colours its age)


@dataclass(frozen=True, slots=True)
class UiTrade:
    status: TradeStatus = "idle"
    message: str = ""
    source: UiTradeStation | None = None  # where you buy (None = no trade found / none asked yet)
    destination: UiTradeStation | None = None  # where you sell
    distance_ly: float | None = None  # 2 decimals
    jumps: int | None = None  # fewest a straight leg takes with the load aboard (None = range not known)
    laden_ly: float | None = None  # load aboard, full tanks, less the safety margin; 2 decimals
    goods: tuple[TradeGood, ...] = ()
    cost: int = 0  # Cr the load costs
    total_profit: int = 0
    cargo: int = 0  # t of hold and Cr the search was run with
    capital: int = 0
    searched: str = ""  # ISO time of the search
    handled: int = 0  # trade requests carried out: the page knows its click was answered
    # what the form starts from: the station you last docked at, your hold and your balance
    docked_system: str = ""
    docked_station: str = ""
    hold: int | None = None
    balance: int | None = None


def ui_station(s: TradeStation) -> UiTradeStation:
    return UiTradeStation(
        system=s.system,
        system_id64="" if s.system_id64 is None else str(s.system_id64),
        station=s.station,
        arrival_ls=None if s.arrival_ls is None else round(s.arrival_ls),
        updated=s.updated,
    )


def parse_trade_request(raw: object) -> TradeRequest | str:
    """POST /api/trade: {system, station, cargo, capital, max_hop_ly, max_arrival_ls, max_age_days, pad,
    carriers, planetary}; cargo / capital null = the ship's hold / your balance. A message when it is not
    one."""
    if not isinstance(raw, dict):
        return "send a JSON object"
    system, station = raw.get("system"), raw.get("station")
    if not isinstance(system, str) or not system.strip() or len(system) > MAX_NAME:
        return "name the system you buy in"
    if not isinstance(station, str) or not station.strip() or len(station) > MAX_NAME:
        return "name the station you buy at"
    cargo, capital = raw.get("cargo"), raw.get("capital")
    if cargo is not None and (type(cargo) is not int or not 1 <= cargo <= MAX_CARGO):
        return f"cargo must be a whole number of tonnes from 1 to {MAX_CARGO:,}"
    if capital is not None and (type(capital) is not int or not 1 <= capital <= MAX_CAPITAL):
        return "credits must be a whole number above 0"
    hop, arrival, age = raw.get("max_hop_ly"), raw.get("max_arrival_ls"), raw.get("max_age_days")
    if isinstance(hop, bool) or not isinstance(hop, int | float) or not 0 < hop <= MAX_HOP_LY:
        return f"distance must be more than 0 and at most {MAX_HOP_LY:g} ly"
    if type(arrival) is not int or not 1 <= arrival <= MAX_ARRIVAL_LS:
        return "arrival distance must be a whole number of ls above 0"
    if isinstance(age, bool) or not isinstance(age, int | float) or not 0 < age <= MAX_AGE_DAYS:
        return f"data age must be more than 0 and at most {MAX_AGE_DAYS:g} days"
    pad = raw.get("pad")
    if pad not in PAD_RANK:
        return "pad must be S, M or L"
    flags = [raw.get("carriers"), raw.get("planetary")]
    if not all(isinstance(f, bool) for f in flags):
        return "carriers and planetary must be true or false"
    return TradeRequest(
        system=" ".join(system.split()),
        station=" ".join(station.split()),
        cargo=cargo,
        capital=capital,
        max_hop_ly=float(hop),
        max_arrival_ls=arrival,
        max_age_days=float(age),
        pad=pad,
        carriers=raw["carriers"],
        planetary=raw["planetary"],
    )
