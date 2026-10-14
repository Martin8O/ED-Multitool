"""Nearest engineer and nearest station with the services you need — pure.

Engineers come from the bundled catalog: every one of them, nearest first from where you are, with what
they improve; the page filters ship / on-foot / module itself. Stations come from Spansh's station search:
the request the page sends (the places to count and the services a station must have), the typed rows,
the pad filter, and what the page's "Nearest service" card shows (`UiState.services`).

A material trader's materials and a technology broker's kind follow the station's primary economy (the
game's rule): Extraction / Refinery → raw, Industrial → manufactured, High Tech / Military → encoded;
High Tech → Guardian broker, Industrial → human broker. Asking for a kind asks Spansh for those economies.
"""

import math
from dataclasses import dataclass, replace
from typing import Literal

from edmultitool.catalog.engineers import ENGINEERS
from edmultitool.core.events import StarPos
from edmultitool.core.market import Standings
from edmultitool.core.route import min_jumps

SETTLEMENT_TYPES = ("Settlement", "Surface Settlement")  # Odyssey's surface settlements (Spansh's spelling)
STATIONS_SHOWN = 10
# services a station can be asked to have, in Spansh's spelling (docs/api-notes.md)
STATION_SERVICES = (
    "Refuel",
    "Repair",
    "Restock",
    "Market",
    "Outfitting",
    "Shipyard",
    "Black Market",
    "Universal Cartographics",
    "Vista Genomics",
    "Interstellar Factors Contact",
    "Material Trader",
    "Technology Broker",
    "Pioneer Supplies",
    "Search and Rescue",
)
SERVICE_LABELS = {"Interstellar Factors Contact": "Interstellar Factors"}  # as the game's station menu says
TRADER, BROKER = "Material Trader", "Technology Broker"
Pad = Literal["S", "M", "L"]
ServiceStatus = Literal["idle", "running", "done", "error"]

PAD_RANK = {"S": 1, "M": 2, "L": 3}
# a kind → the primary economies that give it (Spansh's spelling)
TRADER_KINDS: dict[str, tuple[str, ...]] = {
    "Raw": ("Extraction", "Refinery"),
    "Manufactured": ("Industrial",),
    "Encoded": ("High Tech", "Military"),
}
BROKER_KINDS: dict[str, tuple[str, ...]] = {"Guardian": ("High Tech",), "Human": ("Industrial",)}


def _kind(kinds: dict[str, tuple[str, ...]], economy: str) -> str:
    key = economy.replace(" ", "").casefold()
    return next((k for k, econ in kinds.items() if key in {e.replace(" ", "").casefold() for e in econ}), "")


def trader_kind(economy: str) -> str:
    """ "Raw" / "Manufactured" / "Encoded" ("" = an economy that does not decide it)."""
    return _kind(TRADER_KINDS, economy)


def broker_kind(economy: str) -> str:
    """ "Guardian" / "Human" ("" = an economy that does not decide it)."""
    return _kind(BROKER_KINDS, economy)


def _jumps(d: float | None, range_ly: float | None) -> int | None:
    return min_jumps(d, range_ly) if d is not None and range_ly is not None and range_ly > 0 else None


# --- engineers -----------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class UiModule:
    name: str
    grade: int  # the highest grade this engineer reaches


@dataclass(frozen=True, slots=True)
class UiEngineer:
    name: str
    ship: bool  # False = on-foot (suits and personal weapons)
    system: str
    system_id64: str  # decimal string
    base: str
    base_type: str
    modules: tuple[UiModule, ...]  # highest grade first (ship engineers only)
    distance_ly: float | None = None  # from where you are now, 2 decimals
    jumps: int | None = None  # the fewest jumps a straight leg can take with the routing range


def ui_engineers(star_pos: StarPos | None, range_ly: float | None) -> tuple[UiEngineer, ...]:
    """Every engineer, nearest first (by name when where you are is not known)."""
    rows = []
    for e in ENGINEERS:
        d = None if star_pos is None else round(math.dist(star_pos, e.star_pos), 2)
        mods = sorted(e.modules.items(), key=lambda kv: (-kv[1], kv[0]))
        rows.append(
            UiEngineer(
                name=e.name,
                ship=e.ship,
                system=e.system,
                system_id64=str(e.system_id64),
                base=e.base,
                base_type=e.base_type,
                modules=tuple(UiModule(m, g) for m, g in mods),
                distance_ly=d,
                jumps=_jumps(d, range_ly),
            )
        )
    rows.sort(key=lambda r: (r.distance_ly if r.distance_ly is not None else 0.0, r.name.casefold()))
    return tuple(rows)


# --- stations with the services you need ---------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ServiceRequest:
    """What the page asks for (POST /api/services)."""

    pad: Pad = "L"  # the pad your ship needs
    carriers: bool = False  # fleet carriers too
    settlements: bool = True  # Odyssey surface settlements too
    hide_barred: bool = True  # leave out stations that would refuse you docking
    services: tuple[str, ...] = ()  # every one of these (STATION_SERVICES order); () = any station
    trader_kind: str = ""  # with a Material Trader: "Raw" / "Manufactured" / "Encoded" ("" = any)
    broker_kind: str = ""  # with a Technology Broker: "Guardian" / "Human" ("" = any)

    def economies(self) -> tuple[str, ...] | None:
        """The primary economies the kinds asked for allow: None = any, () = none can (two kinds that never
        meet, as a Raw trader with a Guardian broker)."""
        allowed: set[str] | None = None
        for service, kind, kinds in (
            (TRADER, self.trader_kind, TRADER_KINDS),
            (BROKER, self.broker_kind, BROKER_KINDS),
        ):
            if kind and service in self.services:
                these = set(kinds[kind])
                allowed = these if allowed is None else allowed & these
        return None if allowed is None else tuple(sorted(allowed))


@dataclass(frozen=True, slots=True)
class ServiceQuery:
    """A request with the place it is measured from (the worker's input)."""

    request: ServiceRequest
    system: str
    address: int | None  # its id64
    star_pos: StarPos


@dataclass(frozen=True, slots=True)
class ServiceRow:
    """One station, as Spansh's station search reports it."""

    station: str
    station_type: str
    system: str
    system_id64: int | None
    x: float
    y: float
    z: float
    arrival_ls: float | None
    pad: str  # largest pad: "L" / "M" / "S" / "" (none known)
    economy: str  # primary economy ("High Tech", "Refinery", …; "" = none known)
    updated: str  # ISO time of the station data
    # who runs it, for who may dock (`Standings.barred`):
    faction: str = ""  # controlling minor faction ("" = not known, or a carrier)
    carrier: bool = False
    access: str = ""  # a carrier's docking access, lower case ("all", "friends", …; "" = not known)
    stronghold: bool = False  # a Powerplay Stronghold Carrier
    power: str = ""  # its power


def usable(rows: tuple[ServiceRow, ...] | list[ServiceRow], query: ServiceQuery) -> list[ServiceRow]:
    """Rows whose pad fits, each station once, nearest first."""
    need = PAD_RANK[query.request.pad]
    out: list[ServiceRow] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r.system.casefold(), r.station.casefold())
        if key in seen or PAD_RANK.get(r.pad, 0) < need:
            continue
        seen.add(key)
        out.append(r)
    # nearest system first; within a system, nearest the arrival star first
    out.sort(key=lambda r: (round(math.dist(query.star_pos, (r.x, r.y, r.z)), 2), r.arrival_ls or 0.0))
    return out


@dataclass(frozen=True, slots=True)
class UiServiceRow:
    system: str
    system_id64: str  # decimal string ("" when the source gave none)
    station: str
    station_type: str
    pad: str
    arrival_ls: float | None
    kind: str  # with a trader / broker asked: "Raw" · "Guardian" (joined by " · "); "" otherwise
    updated: str
    faction: str = ""  # the controlling faction
    standing: str = ""  # your standing with it ("" = not known)
    barred: bool = False  # it would refuse you docking (shown only with hide_barred off)
    carrier: bool = False  # a fleet carrier
    power: str = ""  # a Powerplay Stronghold Carrier's power
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    distance_ly: float | None = None
    jumps: int | None = None


@dataclass(frozen=True, slots=True)
class UiServices:
    status: ServiceStatus = "idle"
    message: str = ""
    asked: tuple[str, ...] = ()  # the services the list shown was searched for (Spansh's names)
    trader_kind: str = ""  # and the kinds asked with them ("" = any)
    broker_kind: str = ""
    pad: str = ""  # and the places asked with them ("" = nothing searched yet)
    carriers: bool = False
    settlements: bool = True
    hide_barred: bool = True
    rows: tuple[UiServiceRow, ...] = ()
    source: str = ""  # "Spansh"
    searched: str = ""  # ISO time of the search ("" = none yet)
    searched_from: str = ""  # the system it was measured from
    handled: int = 0  # service requests carried out: the page knows its click was answered
    barred: int = 0  # stations left out because they would refuse you docking


def row_kind(services: tuple[str, ...], economy: str) -> str:
    """The trader's and the broker's kind when they were asked for ("Raw · Guardian"), else ""."""
    kinds = [f(economy) for s, f in ((TRADER, trader_kind), (BROKER, broker_kind)) if s in services]
    return " · ".join(k for k in kinds if k)


def ui_service_rows(
    services: tuple[str, ...], rows: list[ServiceRow], you: Standings | None = None
) -> tuple[UiServiceRow, ...]:
    """`you` = who you are to the stations (standing and who would refuse you docking)."""
    you = you or Standings()
    return tuple(
        UiServiceRow(
            system=r.system,
            system_id64="" if r.system_id64 is None else str(r.system_id64),
            station=r.station,
            station_type=r.station_type,
            pad=r.pad,
            arrival_ls=None if r.arrival_ls is None else round(r.arrival_ls),
            kind=row_kind(services, r.economy),
            updated=r.updated,
            faction=r.faction,
            standing=you.of(r.faction),
            barred=you.barred(r),
            carrier=r.carrier,
            power=r.power,
            x=r.x,
            y=r.y,
            z=r.z,
        )
        for r in rows
    )


def with_distances(services: UiServices, star_pos: StarPos | None, range_ly: float | None) -> UiServices:
    """Distance and fewest jumps from where you are now to every row (None when not known)."""
    if not services.rows:
        return services
    rows = []
    for r in services.rows:
        d = None if star_pos is None else round(math.dist(star_pos, (r.x, r.y, r.z)), 2)
        rows.append(replace(r, distance_ly=d, jumps=_jumps(d, range_ly)))
    out = tuple(rows)
    return services if out == services.rows else replace(services, rows=out)


def split_barred(
    rows: list[ServiceRow], req: ServiceRequest, you: Standings
) -> tuple[list[ServiceRow], list[ServiceRow]]:
    """The rows shown and those left out because they would refuse you docking (`hide_barred`)."""
    if not req.hide_barred:
        return list(rows), []
    return [r for r in rows if not you.barred(r)], [r for r in rows if you.barred(r)]


def what_asked(req: ServiceRequest) -> str:
    """ "a medium pad, Material Trader (Raw) and Black Market" — for the answer line."""
    pad = {"S": "a small", "M": "a medium", "L": "a large"}[req.pad] + " pad"
    names = []
    for s in req.services:
        kind = req.trader_kind if s == TRADER else req.broker_kind if s == BROKER else ""
        names.append(SERVICE_LABELS.get(s, s) + (f" ({kind})" if kind else ""))
    if not names:
        return pad
    return pad + ", " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1])


def parse_service_request(raw: object) -> ServiceRequest | str:
    """POST /api/services: {pad, carriers, settlements, hide_barred, services, trader_kind, broker_kind};
    a message when it is not one."""
    if not isinstance(raw, dict):
        return "send a JSON object"
    pad = raw.get("pad")
    if pad not in PAD_RANK:
        return "pad must be S, M or L"
    flags = {k: raw.get(k, d) for k, d in (("carriers", False), ("settlements", True), ("hide_barred", True))}
    if not all(isinstance(v, bool) for v in flags.values()):
        return "carriers, settlements and hide_barred must be true or false"
    services = raw.get("services", [])
    if not isinstance(services, list) or not all(s in STATION_SERVICES for s in services):
        return "services must be a list of: " + ", ".join(STATION_SERVICES)
    trader, broker = raw.get("trader_kind", ""), raw.get("broker_kind", "")
    if trader not in ("", *TRADER_KINDS):
        return "trader_kind must be Raw, Manufactured, Encoded or empty"
    if broker not in ("", *BROKER_KINDS):
        return "broker_kind must be Guardian, Human or empty"
    wanted = tuple(s for s in STATION_SERVICES if s in services)
    return ServiceRequest(
        pad=pad, **flags, services=wanted,
        trader_kind=trader if TRADER in wanted else "", broker_kind=broker if BROKER in wanted else "",
    )  # fmt: skip
