"""Journal → core events. The ONLY module that knows journal field names.

`parse_event` turns one journal (or Status.json) dict into a typed event, or None if we don't care.
Unknown / malformed entries never raise: they return None and are logged by the caller if needed.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from edmultitool.catalog.fsd import FSD_TABLE, GUARDIAN_BOOST_LY, RATINGS, FsdSpec

StarPos = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class Arrived:
    """We are in a (new) system: Location, FSDJump or CarrierJump."""

    kind: str  # "Location" | "FSDJump" | "CarrierJump"
    system: str
    address: int  # id64 / SystemAddress — the stable key
    star_pos: StarPos
    jump_dist: float | None = None  # FSDJump only
    fuel_used: float | None = None  # FSDJump only
    fuel_level: float | None = None  # FSDJump only (main tank after the jump)
    boosted: bool = False  # FSDJump with synthesis / neutron / white-dwarf boost
    multicrew: bool = False  # journal "Multicrew": we are crew in someone else's ship
    # minor factions present and your standing with each (-100..100), as the journal lists them
    factions: tuple[tuple[str, float], ...] = ()
    # the same factions with what they are going through; the faction controlling the system
    faction_details: tuple["FactionSeen", ...] = ()
    system_faction: str = ""


@dataclass(frozen=True, slots=True)
class FactionSeen:
    """One minor faction in an arrival's `Factions[]`: your standing with it and its state there."""

    name: str
    reputation: float  # -100..100
    state: str = ""  # the main state, e.g. "CivilWar" ("" = none)
    active: tuple[str, ...] = ()  # every active state, e.g. ("Boom", "Expansion")
    influence: float | None = None  # 0..1 in this system


@dataclass(frozen=True, slots=True)
class FsdFit:
    """The fitted FSD after engineering modifiers."""

    item: str
    overcharge: bool
    size: int
    rating: str
    spec: FsdSpec
    engineered: bool


@dataclass(frozen=True, slots=True)
class LoadoutChanged:
    ship_type: str
    ship_name: str
    ship_id: int | None
    unladen_mass: float
    fuel_capacity_main: float
    fuel_capacity_reserve: float
    cargo_capacity: int
    game_max_jump_range: float  # Loadout.MaxJumpRange — unladen best case, never used for routes
    fsd: FsdFit | None
    guardian_boost_ly: float
    ship_ident: str = ""  # the player's registration, e.g. "XC-07Q"


@dataclass(frozen=True, slots=True)
class FuelChanged:
    main: float | None = None  # absolute main-tank level, if known
    reservoir: float | None = None
    added: float | None = None  # refuel amount, when only the delta is known
    ship_id: int | None = None  # which ship this fuel level belongs to (LoadGame)


@dataclass(frozen=True, slots=True)
class ShipNamed:
    """The game told us a ship's display name (LoadGame, ShipyardSwap, ShipyardNew)."""

    symbol: str  # lower-case, as in Loadout.Ship
    name: str


@dataclass(frozen=True, slots=True)
class CargoChanged:
    count: int


@dataclass(frozen=True, slots=True)
class CargoItem:
    """One line of the ship's hold (the game's `Inventory`; mission cargo and stolen goods counted apart)."""

    symbol: str  # lower case, as the journal spells it ("painite", "lowtemperaturediamond", "drones")
    name: str  # the game's display name ("" when it gave none)
    count: int  # t, all of it
    stolen: int = 0  # t of it stolen: only a black market buys these
    mission: bool = False  # cargo a mission gave you to deliver


@dataclass(frozen=True, slots=True)
class CargoHeld:
    """What the ship's hold holds now, line by line (Cargo with `Inventory`, Cargo.json, EDMC's state)."""

    items: tuple[CargoItem, ...]
    # False: only EDMC's summary (tonnes per commodity) was there — mission cargo and stolen goods are not
    # told apart from your own until the game lists the hold again
    detailed: bool = True


@dataclass(frozen=True, slots=True)
class CrewChanged:
    in_other_ship: bool  # True = we are crew in someone else's ship


@dataclass(frozen=True, slots=True)
class GameClosed:
    """Shutdown: the game was quit. Where you were and in what stay known, as the last seen."""


@dataclass(frozen=True, slots=True)
class GameLoaded:
    """LoadGame: fuel of the current ship + its display name. Precedes Location and Loadout."""

    fuel: FuelChanged | None
    ship: ShipNamed | None


@dataclass(frozen=True, slots=True)
class ShipSold:
    """A ship left the fleet (ShipyardSell, ShipyardBuy trading in the old one, SellShipOnRebuy)."""

    ship_id: int


@dataclass(frozen=True, slots=True)
class FleetListed:
    """StoredShips: every ship the player owns apart from the current one."""

    ship_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class BodyMapped:
    """SAAScanComplete: you mapped a body with probes — its mapping value is yours."""

    system_address: int
    body_id: int
    when: str  # ISO time


@dataclass(frozen=True, slots=True)
class SpeciesAnalysed:
    """ScanOrganic "Analyse" (the third sample): this species on this body is yours."""

    system_address: int
    body_id: int
    species: str  # the game's own species name, e.g. "Fonticulua Campestris"
    when: str


@dataclass(frozen=True, slots=True)
class ExplorationSold:
    """Cartographic data sold (SellExplorationData / MultiSellExplorationData), by system NAME: the
    journal names no system ids here."""

    systems: tuple[str, ...]
    when: str


@dataclass(frozen=True, slots=True)
class OrganicSold:
    """Exobiology data sold (SellOrganicData): one entry per sample sold, by species name."""

    species: tuple[str, ...]
    when: str


@dataclass(frozen=True, slots=True)
class Killer:
    """Who killed you: a pilot (with ship and combat rank) or a station / settlement (its type, no rank)."""

    name: str  # the pilot's or the station's name ("" = the game gave none)
    ship: str  # the game's symbol for the ship or the station type ("" = none)
    rank: str  # "Dangerous", … ("" = none)


@dataclass(frozen=True, slots=True)
class Died:
    """The commander died: unsold exploration and exobiology data is lost."""

    when: str
    killers: tuple[Killer, ...] = ()  # one, every wing member, or none (crashed, self-destructed…)


@dataclass(frozen=True, slots=True)
class Resurrected:
    """Back after a death: how (rebuy, free…), what it cost, and whether you could not pay it."""

    option: str  # the game's word, lower case: "rebuy", "free", … ("" = none)
    cost: int
    bankrupt: bool
    when: str


@dataclass(frozen=True, slots=True)
class PowerPledged:
    """The Powerplay power you are pledged to ("" = none): at login, on joining, leaving or defecting."""

    power: str


ExploreEvent = BodyMapped | SpeciesAnalysed | ExplorationSold | OrganicSold | Died

Event = (
    Arrived
    | LoadoutChanged
    | FuelChanged
    | CargoChanged
    | CrewChanged
    | GameClosed
    | ShipNamed
    | GameLoaded
    | ShipSold
    | FleetListed
    | PowerPledged
    | ExploreEvent
)

# What tells who you are to the stations; the market card's history pass pre-filters lines on these: the
# arrivals' reputation field, and the Powerplay pledge events.
REPUTATION_FIELD = "MyReputation"
PLEDGE_EVENTS = ("Powerplay", "PowerplayJoin", "PowerplayLeave", "PowerplayDefect")
# Events that touch the remembered fleet; `edmultitool.history` pre-filters history files on these.
FLEET_EVENTS = ("Loadout", "StoredShips", "ShipyardSell", "ShipyardBuy", "SellShipOnRebuy")
# Events that touch what you have already explored (+ arrivals, which name the system you are in).
EXPLORE_EVENTS = (
    "SAAScanComplete", "ScanOrganic", "SellExplorationData", "MultiSellExplorationData", "SellOrganicData",
    "Died", "Location", "FSDJump", "CarrierJump",
)  # fmt: skip


def _star_pos(raw: Any) -> StarPos | None:
    if isinstance(raw, list | tuple) and len(raw) == 3 and all(isinstance(v, int | float) for v in raw):
        return (float(raw[0]), float(raw[1]), float(raw[2]))
    return None


def parse_fsd(item: str, engineering: dict[str, Any] | None = None) -> FsdFit | None:
    """`int_hyperdrive_overcharge_size5_class5` (+ Engineering block) → FsdFit."""
    parts = item.lower().split("_")
    if len(parts) < 4 or parts[0] != "int" or parts[1] != "hyperdrive":
        return None
    overcharge = parts[2] == "overcharge"
    rest = parts[3:] if overcharge else parts[2:]
    try:
        size = int(rest[0].removeprefix("size"))
        rating = RATINGS[int(rest[1].removeprefix("class")) - 1]
    except (IndexError, ValueError):
        return None
    base = FSD_TABLE.get((overcharge, size, rating))
    if base is None:
        return None
    opt_mass, max_fuel = base.opt_mass, base.max_fuel
    engineered = False
    for mod in (engineering or {}).get("Modifiers", []):
        label, value = mod.get("Label"), mod.get("Value")
        if not isinstance(value, int | float):
            continue
        if label == "FSDOptimalMass":
            opt_mass, engineered = float(value), True
        elif label == "MaxFuelPerJump":
            max_fuel, engineered = float(value), True
    spec = FsdSpec(opt_mass, max_fuel, base.fuel_mul, base.fuel_power)
    return FsdFit(item.lower(), overcharge, size, rating, spec, engineered)


def _guardian_boost(item: str) -> float:
    low = item.lower()
    if not low.startswith("int_guardianfsdbooster_size"):
        return 0.0
    try:
        return GUARDIAN_BOOST_LY.get(int(low.removeprefix("int_guardianfsdbooster_size")), 0.0)
    except ValueError:
        return 0.0


# Status.json Flags: in fighter / in SRV — their fuel is not our ship's fuel.
_FLAG_IN_FIGHTER = 1 << 25
_FLAG_IN_SRV = 1 << 26
# Status.json Flags2 (Odyssey): on foot / in an Apex taxi / crew in someone else's ship — same reason.
_FLAG2_ON_FOOT = 1 << 0
_FLAG2_IN_TAXI = 1 << 1
_FLAG2_IN_MULTICREW = 1 << 2


# Suits on foot ("ExplorationSuit_Class1", "FlightSuit") — LoadGame names the suit when you log in on foot.
_SUIT = re.compile(r"(flight|utility|tactical|exploration)suit", re.IGNORECASE)


def is_ship(symbol: object) -> bool:
    """A ship the player owns and jumps with: not a suit, SRV, fighter or Apex taxi."""
    low = str(symbol).lower()
    if not low or _SUIT.search(low):
        return False
    return not any(word in low for word in ("buggy", "fighter", "taxi", "_srv"))


def _loadout(e: dict[str, Any]) -> LoadoutChanged | None:
    if not is_ship(e.get("Ship", "")):
        return None  # SRV / fighter / taxi loadout: not the ship we jump with
    try:
        fsd: FsdFit | None = None
        boost = 0.0
        for m in e.get("Modules", []):
            item = str(m.get("Item", ""))
            if m.get("Slot") == "FrameShiftDrive":
                fsd = parse_fsd(item, m.get("Engineering"))
            boost = max(boost, _guardian_boost(item))
        fuel = e.get("FuelCapacity") or {}
        return LoadoutChanged(
            ship_type=str(e["Ship"]),
            ship_name=str(e.get("ShipName", "")),
            ship_id=e.get("ShipID"),
            unladen_mass=float(e["UnladenMass"]),
            fuel_capacity_main=float(fuel.get("Main", 0.0)),
            fuel_capacity_reserve=float(fuel.get("Reserve", 0.0)),
            cargo_capacity=int(e.get("CargoCapacity", 0)),
            game_max_jump_range=float(e.get("MaxJumpRange", 0.0)),
            fsd=fsd,
            guardian_boost_ly=boost,
            ship_ident=str(e.get("ShipIdent") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _arrived(e: dict[str, Any]) -> Arrived | None:
    pos = _star_pos(e.get("StarPos"))
    system, address = e.get("StarSystem"), e.get("SystemAddress")
    if pos is None or not isinstance(system, str) or not isinstance(address, int):
        return None
    kind = str(e["event"])
    multicrew = e.get("Multicrew") is True
    details = _faction_details(e.get("Factions"))
    owner = e.get("SystemFaction")
    arrived = Arrived(
        kind,
        system,
        address,
        pos,
        multicrew=multicrew,
        factions=tuple((f.name, f.reputation) for f in details),
        faction_details=details,
        system_faction=_text(owner.get("Name")) if isinstance(owner, dict) else "",
    )
    if kind != "FSDJump" or e.get("Taxi") is True:  # a taxi's jump: you arrive, its fuel is not yours
        return arrived
    return replace(
        arrived,
        jump_dist=_num(e.get("JumpDist")),
        fuel_used=_num(e.get("FuelUsed")),
        fuel_level=_num(e.get("FuelLevel")),
        boosted=bool(e.get("BoostUsed")),
    )


def _faction_details(raw: Any) -> tuple[FactionSeen, ...]:
    """`Factions[]` of an arrival: each faction's `Name`, your `MyReputation` with it, its `FactionState`,
    `ActiveStates[].State` and `Influence`. A faction without a reputation is left out."""
    if not isinstance(raw, list):
        return ()
    out = []
    for f in raw:
        if isinstance(f, dict) and isinstance(f.get("Name"), str) and f["Name"]:
            rep = _num(f.get(REPUTATION_FIELD))
            if rep is not None:
                state = _text(f.get("FactionState"))
                states = f.get("ActiveStates")
                rows = states if isinstance(states, list) else []
                active = tuple(
                    _text(s.get("State")) for s in rows if isinstance(s, dict) and _text(s.get("State"))
                )
                state = "" if state == "None" else state
                out.append(FactionSeen(f["Name"], rep, state, active, _num(f.get("Influence"))))
    return tuple(out)


def _ship_named(symbol: Any, name: Any) -> ShipNamed | None:
    if isinstance(symbol, str) and isinstance(name, str) and symbol and name.strip():
        return ShipNamed(symbol.lower(), name.strip())
    return None


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) else None


def parse_event(e: dict[str, Any]) -> Event | None:
    """One journal entry (or a Status.json snapshot with event == "Status") → event or None."""
    name = e.get("event")
    match name:
        case "Location" | "FSDJump" | "CarrierJump":
            return _arrived(e)
        case "Loadout":
            return _loadout(e)
        case "LoadGame":
            if "Ship" in e and not is_ship(e["Ship"]):
                return None  # logged in on foot (or in an SRV): the suit's "fuel" is not the ship's
            level = _num(e.get("FuelLevel"))
            ship_id = e.get("ShipID")
            fuel = (
                FuelChanged(main=level, ship_id=ship_id if isinstance(ship_id, int) else None)
                if level is not None
                else None
            )
            named = _ship_named(e.get("Ship"), e.get("Ship_Localised"))
            return GameLoaded(fuel, named) if fuel or named else None
        case "ShipyardSwap" | "ShipyardNew":
            return _ship_named(e.get("ShipType"), e.get("ShipType_Localised"))
        case "FuelScoop":
            total = _num(e.get("Total"))
            return FuelChanged(main=total) if total is not None else None
        case "RefuelAll" | "RefuelPartial":
            amount = _num(e.get("Amount"))
            return FuelChanged(added=amount) if amount is not None else None
        case "Cargo":
            if e.get("Vessel", "Ship") != "Ship" or not isinstance(e.get("Count"), int):
                return None
            return CargoChanged(int(e["Count"]))
        case "Status":
            fuel = e.get("Fuel")
            flags, flags2 = e.get("Flags"), e.get("Flags2")
            if (
                not isinstance(fuel, dict)
                or (isinstance(flags, int) and flags & (_FLAG_IN_FIGHTER | _FLAG_IN_SRV))
                or (
                    isinstance(flags2, int)
                    and flags2 & (_FLAG2_ON_FOOT | _FLAG2_IN_TAXI | _FLAG2_IN_MULTICREW)
                )
            ):
                return None
            return FuelChanged(main=_num(fuel.get("FuelMain")), reservoir=_num(fuel.get("FuelReservoir")))
        case "ShipyardSell" | "ShipyardBuy":
            sold = e.get("SellShipID")
            return ShipSold(sold) if isinstance(sold, int) else None
        case "SellShipOnRebuy":
            sold = e.get("SellShipId")
            return ShipSold(sold) if isinstance(sold, int) else None
        case "StoredShips":
            ids = [
                s.get("ShipID")
                for key in ("ShipsHere", "ShipsRemote")
                for s in (e.get(key) or [])
                if isinstance(s, dict)
            ]
            return FleetListed(frozenset(i for i in ids if isinstance(i, int)))
        case "Powerplay" | "PowerplayJoin" | "PowerplayDefect":
            power = e.get("ToPower") if name == "PowerplayDefect" else e.get("Power")
            return PowerPledged(power) if isinstance(power, str) and power else None
        case "PowerplayLeave":
            return PowerPledged("")
        case "Shutdown":
            return GameClosed()
        case "JoinACrew":
            return CrewChanged(in_other_ship=True)
        case "QuitACrew" | "EndCrewSession":
            return CrewChanged(in_other_ship=False)
        case (
            "SAAScanComplete" | "ScanOrganic" | "SellExplorationData" | "MultiSellExplorationData"
            | "SellOrganicData" | "Died"
        ):  # fmt: skip
            return _explored(e)
    return None


def _explored(e: dict[str, Any]) -> ExploreEvent | None:
    when = entry_time(e)
    if not when:
        return None  # the rules that undo a death or a sale compare times
    match e.get("event"):
        case "SAAScanComplete":
            address, body = e.get("SystemAddress"), e.get("BodyID")
            if isinstance(address, int) and isinstance(body, int):
                return BodyMapped(address, body, when)
        case "ScanOrganic":
            address, body, name = e.get("SystemAddress"), e.get("Body"), e.get("Species_Localised")
            analysed = e.get("ScanType") == "Analyse" and isinstance(name, str) and name.strip()
            if analysed and isinstance(address, int) and isinstance(body, int) and isinstance(name, str):
                return SpeciesAnalysed(address, body, name.strip(), when)
        case "SellExplorationData":
            systems = e.get("Systems")
            if isinstance(systems, list):
                return ExplorationSold(tuple(s for s in systems if isinstance(s, str)), when)
        case "MultiSellExplorationData":
            rows = e.get("Discovered")
            if isinstance(rows, list):
                names = (r.get("SystemName") for r in rows if isinstance(r, dict))
                return ExplorationSold(tuple(n for n in names if isinstance(n, str)), when)
        case "SellOrganicData":
            rows = e.get("BioData")
            if isinstance(rows, list):
                names = (r.get("Species_Localised") for r in rows if isinstance(r, dict))
                return OrganicSold(tuple(n.strip() for n in names if isinstance(n, str) and n.strip()), when)
        case "Died":
            return _died(e, when)
    return None


def _died(e: dict[str, Any], when: str) -> Died:
    def killer(name: Any, localised: Any, ship: Any, rank: Any) -> Killer:
        shown = localised if isinstance(localised, str) and localised.strip() else name
        return Killer(_text(shown), _text(ship).lower(), _text(rank))

    wing = e.get("Killers")
    if isinstance(wing, list):
        rows = (r for r in wing if isinstance(r, dict))
        killers = tuple(
            killer(r.get("Name"), r.get("Name_Localised"), r.get("Ship"), r.get("Rank")) for r in rows
        )
    else:
        one = killer(
            e.get("KillerName"), e.get("KillerName_Localised"), e.get("KillerShip"), e.get("KillerRank")
        )
        killers = (one,) if one.name or one.ship else ()
    return Died(when, tuple(k for k in killers if k.name or k.ship))


# The journal events `edmultitool.history` pre-filters on for the Stats tab's deaths.
DEATH_EVENTS = ("Died", "Resurrect")


def parse_death(e: dict[str, Any]) -> Died | Resurrected | None:
    """A death (with who killed you) or the resurrection after one; None for any other entry."""
    when = entry_time(e)
    if not when:
        return None
    match e.get("event"):
        case "Died":
            return _died(e, when)
        case "Resurrect":
            cost = _num(e.get("Cost"))
            return Resurrected(
                _text(e.get("Option")).lower(), int(cost or 0), e.get("Bankrupt") is True, when
            )
    return None


@dataclass(frozen=True, slots=True)
class NavPoint:
    name: str
    address: int  # id64
    pos: StarPos
    star_class: str  # e.g. "K", "M_RedGiant", "TTS", "N"


@dataclass(frozen=True, slots=True)
class GameRoute:
    """The route plotted in the game's galaxy map (NavRoute.json), the system you were in first;
    `points` empty = the route was cleared (NavRouteClear)."""

    points: tuple[NavPoint, ...]
    plotted: str  # ISO time


def nav_route_pending(e: dict[str, Any]) -> bool:
    """A journal NavRoute entry that does not carry the route itself: it is in NavRoute.json."""
    return e.get("event") == "NavRoute" and not isinstance(e.get("Route"), list)


def parse_nav_route(e: dict[str, Any]) -> GameRoute | None:
    """NavRoute (journal entry with "Route", or NavRoute.json) / NavRouteClear → GameRoute, else None."""
    match e.get("event"):
        case "NavRouteClear":
            return GameRoute((), entry_time(e))
        case "NavRoute" if isinstance(e.get("Route"), list):
            out = []
            for p in e["Route"]:
                if not isinstance(p, dict):
                    return None
                pos, address, name = _star_pos(p.get("StarPos")), p.get("SystemAddress"), p.get("StarSystem")
                if pos is None or not isinstance(address, int) or not isinstance(name, str):
                    return None
                star = p.get("StarClass")
                out.append(NavPoint(name, address, pos, star if isinstance(star, str) else ""))
            return GameRoute(tuple(out), entry_time(e))
    return None


def entry_time(e: dict[str, Any]) -> str:
    """The entry's ISO timestamp ("" when missing): orders ships by when they were last flown."""
    stamp = e.get("timestamp")
    return stamp if isinstance(stamp, str) else ""


def edmc_ship_differs(st: dict[str, Any], ship: LoadoutChanged | None) -> bool:
    """Cheap test, run on every entry: does EDMC's `state` hold another ship or build than `ship`?"""
    ship_id, mass, jump = st.get("ShipID"), st.get("UnladenMass"), st.get("MaxJumpRange")
    if not isinstance(ship_id, int) or not isinstance(mass, int | float):
        return False
    if ship is None or ship.ship_id != ship_id or abs(ship.unladen_mass - mass) > 1e-6:
        return True
    return isinstance(jump, int | float) and abs(ship.game_max_jump_range - jump) > 1e-6


def loadout_from_edmc_state(st: dict[str, Any]) -> LoadoutChanged | None:
    """The current ship as EDMC's cumulative `state` holds it, or None when that is incomplete.

    EDMC updates `state` before it runs its own ship-loadout export; when that export fails, plugins never
    get the `Loadout` entry, but every later entry arrives with the new ship in `state`.
    Right after a shipyard swap or purchase EDMC clears `Modules` until the next Loadout: None then.
    """
    modules = st.get("Modules")
    ship_id = st.get("ShipID")
    if not (st.get("ShipType") and st.get("UnladenMass") and isinstance(modules, dict)):
        return None
    if not isinstance(ship_id, int):
        return None
    fuel_cap = st.get("FuelCapacity") or {}
    return _loadout(
        {
            "event": "Loadout",
            "Ship": st["ShipType"],
            "ShipID": ship_id,
            "ShipName": st.get("ShipName") or "",
            "ShipIdent": st.get("ShipIdent") or "",
            "UnladenMass": st["UnladenMass"],
            "FuelCapacity": {k: v for k, v in fuel_cap.items() if isinstance(v, int | float)},
            "CargoCapacity": st.get("CargoCapacity") or 0,
            "MaxJumpRange": st.get("MaxJumpRange") or 0.0,
            "Modules": list(modules.values()),
        }
    )


def events_from_edmc_state(st: dict[str, Any]) -> list[Event]:
    """EDMC's cumulative `state` dict → the events that rebuild it.

    Used when EDMC starts while the game is already running (synthetic "StartUp" entry): the earlier
    journal events were not delivered to plugins, but EDMC's state already reflects them.
    EDMC keeps fuel LEVEL out of `state`; it arrives via the dashboard (Status.json) instead.
    """
    events: list[Event] = []
    in_crew = bool(st.get("Captain"))
    if in_crew:
        events.append(CrewChanged(in_other_ship=True))
    loadout = loadout_from_edmc_state(st)
    if loadout is not None:
        events.append(loadout)
    if not in_crew:
        arrived = _arrived(
            {
                "event": "Location",
                "StarSystem": st.get("SystemName"),
                "SystemAddress": st.get("SystemAddress"),
                "StarPos": list(st["StarPos"]) if isinstance(st.get("StarPos"), list | tuple) else None,
            }
        )
        if arrived is not None:
            events.append(arrived)
    cargo = st.get("Cargo")
    if isinstance(cargo, dict):
        events.append(CargoChanged(sum(v for v in cargo.values() if isinstance(v, int))))
    return events


# --- the hold, line by line -------------------------------------------------------------------------


def parse_cargo(e: dict[str, Any]) -> CargoHeld | None:
    """A ship's `Cargo` entry (journal or Cargo.json) that lists its `Inventory` → the hold; None otherwise
    (the SRV's hold, or a Cargo entry that only gives the count: the list is then in Cargo.json)."""
    if e.get("event") != "Cargo" or e.get("Vessel", "Ship") != "Ship":
        return None
    inventory = e.get("Inventory")
    if not isinstance(inventory, list):
        return None
    items: list[CargoItem] = []
    for row in inventory:
        if not isinstance(row, dict) or not isinstance(row.get("Name"), str):
            continue
        count = row.get("Count")
        if type(count) is not int or count <= 0:
            continue
        stolen = row.get("Stolen")
        local = row.get("Name_Localised")
        items.append(
            CargoItem(
                symbol=row["Name"].strip().lower(),
                name=local.strip() if isinstance(local, str) else "",
                count=count,
                stolen=min(stolen, count) if type(stolen) is int and stolen > 0 else 0,
                mission="MissionID" in row,
            )
        )
    return CargoHeld(tuple(items))


def cargo_from_edmc_state(st: dict[str, Any]) -> CargoHeld | None:
    """The hold as EDMC's cumulative `state` holds it: the Cargo.json it read last (`CargoJSON`, every line
    with stolen and mission cargo apart), else its plain symbol → tonnes count; None when it holds neither."""
    raw = st.get("CargoJSON")
    if isinstance(raw, dict):
        held = parse_cargo({**raw, "event": "Cargo"})
        if held is not None:
            return held
    cargo = st.get("Cargo")
    if not isinstance(cargo, dict):
        return None
    items = tuple(
        CargoItem(symbol=str(k).strip().lower(), name="", count=v)
        for k, v in cargo.items()
        if type(v) is int and v > 0
    )
    return CargoHeld(items, detailed=False)


# --- the commander's money and legal standing ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreditsKnown:
    """Your balance: LoadGame `Credits` at login, Status.json `Balance` live (also on foot)."""

    credits: int


@dataclass(frozen=True, slots=True)
class LegalStateKnown:
    """Status.json `LegalState` in the system you are in: Clean, IllegalCargo, Speeding, Wanted, Hostile,
    PassengerWanted, Warrant, Thargoid, Allied."""

    state: str


@dataclass(frozen=True, slots=True)
class NotorietyKnown:
    """Statistics `Crime.Notoriety` (0-10), written once at login."""

    level: int


@dataclass(frozen=True, slots=True)
class CrimeCommitted:
    """CommitCrime: a fine and/or a bounty from `faction`."""

    faction: str
    fine: int
    bounty: int
    when: str


@dataclass(frozen=True, slots=True)
class DebtPaid:
    """PayFines / PayBounties / PayLegacyFines. `faction` "" = every faction; `bounties` False = only fines.
    Paying bounties at a station or Interstellar Factors settles that faction's fines too (the amount paid is
    their sum plus the broker's cut)."""

    faction: str
    bounties: bool
    when: str


CommanderEvent = CreditsKnown | LegalStateKnown | NotorietyKnown | CrimeCommitted | DebtPaid | Died

# The journal events that touch money or legal standing; `edmultitool.history` pre-filters on these.
COMMANDER_EVENTS = (
    "LoadGame",
    "Statistics",
    "CommitCrime",
    "PayFines",
    "PayBounties",
    "PayLegacyFines",
    "Died",
)


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def parse_commander(e: dict[str, Any]) -> list[CommanderEvent]:
    """One journal entry (or a Status.json snapshot with event == "Status") → what it says about your money
    and legal standing; [] when nothing."""
    when = entry_time(e)
    match e.get("event"):
        case "Status":
            out: list[CommanderEvent] = []
            balance, legal = _int(e.get("Balance")), e.get("LegalState")
            if balance is not None:
                out.append(CreditsKnown(balance))
            if isinstance(legal, str) and legal:
                out.append(LegalStateKnown(legal))
            return out
        case "LoadGame":
            credits = _int(e.get("Credits"))
            return [CreditsKnown(credits)] if credits is not None else []
        case "Statistics":
            crime = e.get("Crime")
            level = _int(crime.get("Notoriety")) if isinstance(crime, dict) else None
            return [NotorietyKnown(level)] if level is not None else []
        case "CommitCrime":
            faction = e.get("Faction")
            fine, bounty = _int(e.get("Fine")) or 0, _int(e.get("Bounty")) or 0
            if isinstance(faction, str) and faction and (fine > 0 or bounty > 0):
                return [CrimeCommitted(faction, fine, bounty, when)]
        case "PayFines" | "PayBounties":
            faction = e.get("Faction")
            everyone = e.get("AllFines") is True or not isinstance(faction, str) or not faction
            return [DebtPaid("" if everyone else str(faction), e.get("event") == "PayBounties", when)]
        case "PayLegacyFines":
            return [DebtPaid("", True, when)]
        case "Died":
            return [_died(e, when)]
    return []


# --- the commander's career: superpower reputation, ranks, Powerplay -------------------------------


@dataclass(frozen=True, slots=True)
class ReputationKnown:
    """Reputation (at login): your standing with each superpower, -100..100."""

    standings: tuple[tuple[str, float], ...]  # ("Federation", 50.6), … as the journal lists them
    when: str


@dataclass(frozen=True, slots=True)
class RanksKnown:
    """Rank (at login, every rank), Progress (at login, percent to the next rank of each) or Promotion
    (one rank rose; its progress starts again from 0)."""

    values: tuple[tuple[str, int], ...]  # ("Federation", 2), …
    progress: bool  # True = the values are percents to the next rank (Progress)
    when: str


@dataclass(frozen=True, slots=True)
class PowerplayKnown:
    """Powerplay (at login): the power you are pledged to, your merits in all, how long ago you pledged and
    your rank (0 until the power's introductory missions are done)."""

    power: str
    merits: int
    seconds_pledged: int
    when: str
    rank: int | None = None


@dataclass(frozen=True, slots=True)
class MeritsEarned:
    """PowerplayMerits: merits earned for your power just now, and the new total."""

    power: str
    gained: int
    total: int | None
    when: str


@dataclass(frozen=True, slots=True)
class PledgeChanged:
    """PowerplayJoin / PowerplayDefect (`power` = the new one) or PowerplayLeave (`power` "")."""

    power: str
    when: str


@dataclass(frozen=True, slots=True)
class PowerRanked:
    """PowerplayRank: your rank with the power now (written at the pledge too, as 0)."""

    power: str
    rank: int
    when: str


CareerEvent = ReputationKnown | RanksKnown | PowerplayKnown | MeritsEarned | PledgeChanged | PowerRanked

# The journal events of the career; `edmultitool.history` pre-filters on these.
CAREER_EVENTS = (
    "Reputation", "Rank", "Progress", "Promotion",
    "Powerplay", "PowerplayMerits", "PowerplayJoin", "PowerplayDefect", "PowerplayLeave", "PowerplayRank",
)  # fmt: skip
SUPERPOWERS = ("Federation", "Empire", "Alliance", "Independent")
RANKS = ("Combat", "Trade", "Explore", "Soldier", "Exobiologist", "Empire", "Federation", "CQC")


def _named(e: dict[str, Any], names: tuple[str, ...], kind: type) -> tuple[tuple[str, Any], ...]:
    found = []
    for key in names:
        v = e.get(key)
        if isinstance(v, int | float) and not isinstance(v, bool):
            found.append((key, kind(v)))
    return tuple(found)


def parse_career(e: dict[str, Any]) -> list[CareerEvent]:
    """One journal entry → what it says about your reputation, ranks and Powerplay career; [] when nothing.
    Every event carries its time: the history over time is drawn from it."""
    when = entry_time(e)
    if not when:
        return []
    match e.get("event"):
        case "Reputation":
            standings = _named(e, SUPERPOWERS, float)
            return [ReputationKnown(standings, when)] if standings else []
        case "Rank" | "Progress" | "Promotion":
            values = _named(e, RANKS, int)
            return [RanksKnown(values, e.get("event") == "Progress", when)] if values else []
        case "Powerplay":
            power, merits, seconds = e.get("Power"), _int(e.get("Merits")), _int(e.get("TimePledged"))
            if isinstance(power, str) and power and merits is not None:
                return [PowerplayKnown(power, merits, seconds or 0, when, _int(e.get("Rank")))]
        case "PowerplayMerits":
            power, gained = e.get("Power"), _int(e.get("MeritsGained"))
            if isinstance(power, str) and power and gained is not None:
                return [MeritsEarned(power, gained, _int(e.get("TotalMerits")), when)]
        case "PowerplayJoin" | "PowerplayDefect":
            power = e.get("ToPower") if e.get("event") == "PowerplayDefect" else e.get("Power")
            if isinstance(power, str) and power:
                return [PledgeChanged(power, when)]
        case "PowerplayLeave":
            return [PledgeChanged("", when)]
        case "PowerplayRank":
            power, rank = e.get("Power"), _int(e.get("Rank"))
            if isinstance(power, str) and power and rank is not None:
                return [PowerRanked(power, rank, when)]
    return []


# --- surveying the system you are in: FSS / DSS results ---------------------------------------------


@dataclass(frozen=True, slots=True)
class SystemSurveyed:
    """FSSDiscoveryScan (the honk): how many bodies the system has."""

    system_address: int
    body_count: int


@dataclass(frozen=True, slots=True)
class AllBodiesFound:
    """FSSAllBodiesFound: every body of the system is scanned."""

    system_address: int


@dataclass(frozen=True, slots=True)
class BodyScanned:
    """Scan of a star or a planet (rings and belt clusters are left out)."""

    system_address: int
    body_id: int
    name: str
    star_type: str  # the journal's StarType ("K", "N", "DA"…); "" for a planet
    planet_class: str  # the journal's PlanetClass ("High metal content body"…); "" for a star
    mass: float  # Earth masses for a planet, solar masses for a star
    terraformable: bool
    landable: bool
    distance_ls: float
    discovered: bool | None  # someone scanned it before you (None: the entry does not say)
    mapped: bool | None  # someone mapped it before you


@dataclass(frozen=True, slots=True)
class BodySignals:
    """FSSBodySignals (FSS) / SAASignalsFound (after mapping, with the genera): signals on one body."""

    system_address: int
    body_id: int
    name: str
    biological: int
    genera: tuple[str, ...]  # codex keys or the game's names, as the entry gives them ("" when not known)


SurveyEvent = SystemSurveyed | AllBodiesFound | BodyScanned | BodySignals

_BIOLOGICAL = "$SAA_SignalType_Biological;"


def parse_survey(e: dict[str, Any]) -> SurveyEvent | None:
    """One journal entry → what it tells about the bodies of the system you are in, or None."""
    address = e.get("SystemAddress")
    if not isinstance(address, int):
        return None
    match e.get("event"):
        case "FSSDiscoveryScan":
            count = e.get("BodyCount")
            return SystemSurveyed(address, count) if isinstance(count, int) else None
        case "FSSAllBodiesFound":
            return AllBodiesFound(address)
        case "Scan":
            return _scanned(e, address)
        case "FSSBodySignals" | "SAASignalsFound":
            body, name, signals = e.get("BodyID"), e.get("BodyName"), e.get("Signals")
            if not isinstance(body, int) or not isinstance(signals, list):
                return None
            bio = sum(
                s.get("Count", 0)
                for s in signals
                if isinstance(s, dict) and s.get("Type") == _BIOLOGICAL and isinstance(s.get("Count"), int)
            )
            genera = tuple(
                str(g.get("Genus_Localised") or g.get("Genus"))
                for g in e.get("Genuses") or []
                if isinstance(g, dict) and isinstance(g.get("Genus_Localised") or g.get("Genus"), str)
            )
            return BodySignals(address, body, name if isinstance(name, str) else "", bio, genera)
    return None


def _scanned(e: dict[str, Any], address: int) -> BodyScanned | None:
    body, name = e.get("BodyID"), e.get("BodyName")
    star, planet = e.get("StarType"), e.get("PlanetClass")
    if not isinstance(body, int) or not isinstance(name, str):
        return None
    if isinstance(star, str) and star:
        mass, planet = _num(e.get("StellarMass")), ""
    elif isinstance(planet, str) and planet:
        mass, star = _num(e.get("MassEM")), ""
    else:
        return None  # a belt cluster or a ring
    discovered, mapped = e.get("WasDiscovered"), e.get("WasMapped")
    return BodyScanned(
        address,
        body,
        name,
        star_type=star,
        planet_class=planet,
        mass=mass or 0.0,
        terraformable=e.get("TerraformState") in ("Terraformable", "Terraforming"),
        landable=e.get("Landable") is True,
        distance_ls=_num(e.get("DistanceFromArrivalLS")) or 0.0,
        discovered=discovered if isinstance(discovered, bool) else None,
        mapped=mapped if isinstance(mapped, bool) else None,
    )


# --- money: what you earned and spent, and the data you still carry --------------------------------

EARN_KINDS = ("exploration", "exobiology", "trade", "bounties", "missions", "other")
SPEND_KINDS = ("repairs", "fuel", "rearm", "ships", "fines", "rebuy", "other")


@dataclass(frozen=True, slots=True)
class MoneyMoved:
    """Credits earned (`earned`) or spent, of one kind. `amount` may be below 0: a trade sold at a loss, a
    module sold back (money returned within "ships")."""

    earned: bool
    kind: str  # one of EARN_KINDS / SPEND_KINDS
    amount: int
    when: str


@dataclass(frozen=True, slots=True)
class CashMoved:
    """Credits that change your balance without being earned or spent: cargo bought (< 0), and the price paid
    for cargo coming back when it is sold (its profit is a MoneyMoved)."""

    amount: int
    when: str


@dataclass(frozen=True, slots=True)
class BalanceKnown:
    """LoadGame `Credits`: your exact balance at login."""

    credits: int
    when: str


@dataclass(frozen=True, slots=True)
class SessionStarted:
    """LoadGame: the game was started (a session is from here to the next one)."""

    when: str


@dataclass(frozen=True, slots=True)
class SessionEnded:
    """Shutdown: the game was closed."""

    when: str


@dataclass(frozen=True, slots=True)
class DataScanned:
    """Scan of a star or a planet you carry as unsold cartographic data (nav beacon data is left out)."""

    system: str  # by NAME: sales name systems, never their ids
    body: BodyScanned
    when: str


@dataclass(frozen=True, slots=True)
class DataSurveyed:
    """FSSDiscoveryScan (the honk): how many bodies the system has — each adds to its sale, scanned or not."""

    system: str
    system_address: int
    body_count: int
    when: str


@dataclass(frozen=True, slots=True)
class DataMapped:
    """SAAScanComplete: you mapped a body; `efficient` = within the game's probe target (x1.25)."""

    system_address: int
    body_id: int
    efficient: bool
    when: str


@dataclass(frozen=True, slots=True)
class Refined:
    """MiningRefined: one tonne of a commodity refined into the hold."""

    symbol: str  # lower case ("painite"; the journal writes "$painite_name;")
    name: str  # the game's display name ("" when it gave none)
    when: str


MoneyEvent = (
    MoneyMoved | CashMoved | BalanceKnown | SessionStarted | SessionEnded | DataScanned | DataSurveyed
    | DataMapped | SpeciesAnalysed | ExplorationSold | OrganicSold | Died | Refined
)  # fmt: skip

# what one journal entry pays you or costs you: event → (earned, kind, the field holding the amount)
_EARNED = {
    "RedeemVoucher": "Amount",  # kind by the voucher's Type
    "MissionCompleted": "Reward",
    "SearchAndRescue": "Reward",
    "CommunityGoalReward": "Reward",
    "PowerplaySalary": "Amount",
    "SellMicroResources": "Price",
    "SellWeapon": "Price",
    "SellSuit": "Price",
}
_SPENT: dict[str, tuple[str, str]] = {
    "RepairAll": ("repairs", "Cost"),
    "Repair": ("repairs", "Cost"),
    "RefuelAll": ("fuel", "Cost"),
    "RefuelPartial": ("fuel", "Cost"),
    "BuyAmmo": ("rearm", "Cost"),
    "RestockVehicle": ("rearm", "Cost"),
    "BuyDrones": ("rearm", "TotalCost"),
    "ShipyardTransfer": ("ships", "TransferPrice"),
    "FetchRemoteModule": ("ships", "TransferCost"),
    "ModuleRetrieve": ("ships", "Cost"),
    "ModuleStore": ("ships", "Cost"),
    "ModuleBuyAndStore": ("ships", "BuyPrice"),
    "PayFines": ("fines", "Amount"),
    "PayBounties": ("fines", "Amount"),
    "PayLegacyFines": ("fines", "Amount"),
    "MissionFailed": ("fines", "Fine"),
    "MissionAbandoned": ("fines", "Fine"),
    "Resurrect": ("rebuy", "Cost"),
    "CrewHire": ("other", "Cost"),
    "NpcCrewPaidWage": ("other", "Amount"),
    "BuyTradeData": ("other", "Cost"),
    "BuyExplorationData": ("other", "Cost"),
    "BuySuit": ("other", "Price"),
    "BuyWeapon": ("other", "Price"),
    "UpgradeSuit": ("other", "Cost"),
    "UpgradeWeapon": ("other", "Cost"),
    "CarrierBuy": ("other", "Price"),
    "PowerplayFastTrack": ("other", "Cost"),
}
# money given back within a spending kind
_RETURNED: dict[str, tuple[str, str]] = {
    "ModuleSell": ("ships", "SellPrice"),
    "ModuleSellRemote": ("ships", "SellPrice"),
    "ShipyardSell": ("ships", "ShipPrice"),
    "SellShipOnRebuy": ("ships", "ShipPrice"),
    "SellDrones": ("rearm", "TotalSale"),
}
_COMBAT_VOUCHERS = ("bounty", "CombatBond")

# The journal events that touch money or carried data; `edmultitool.history` pre-filters on these.
MONEY_EVENTS = (
    *_EARNED, *_SPENT, *_RETURNED, "MarketSell", "MarketBuy", "ModuleBuy", "ShipyardBuy", "BuyMicroResources",
    "EngineerContribution", "SellExplorationData", "MultiSellExplorationData", "SellOrganicData", "LoadGame",
    "Shutdown", "Scan", "FSSDiscoveryScan", "SAAScanComplete", "ScanOrganic", "Died", "MiningRefined",
)  # fmt: skip
_SYMBOL = re.compile(r"^\$(.+)_name;$", re.IGNORECASE)


def parse_money(e: dict[str, Any]) -> list[MoneyEvent]:
    """One journal entry → the money it moved and the carried data it adds or clears; [] when nothing."""
    when = entry_time(e)
    if not when:
        return []  # every window and every "since the last sale" compares times
    name = e.get("event")
    out: list[MoneyEvent] = []

    def money(earned: bool, kind: str, amount: int | None) -> None:
        if amount:
            out.append(MoneyMoved(earned, kind, amount, when))

    if not isinstance(name, str):
        return out
    if name in _EARNED:
        kind = "other"
        if name == "RedeemVoucher":
            kind = "bounties" if e.get("Type") in _COMBAT_VOUCHERS else "other"
        elif name == "MissionCompleted":
            kind = "missions"
            money(False, "other", _int(e.get("Donated")))
        money(True, kind, _int(e.get(_EARNED[name])))
    elif name in _SPENT:
        kind, key = _SPENT[name]
        money(False, kind, _int(e.get(key)))
    elif name in _RETURNED:
        kind, key = _RETURNED[name]
        paid = _int(e.get(key))
        money(False, kind, -paid if paid else None)
    match name:
        case "MarketSell":
            total, count, paid = _int(e.get("TotalSale")), _int(e.get("Count")), _int(e.get("AvgPricePaid"))
            if total is not None:
                money(True, "trade", total - (paid or 0) * (count or 0))
                if paid and count:
                    out.append(CashMoved(paid * count, when))
        case "MarketBuy":
            cost = _int(e.get("TotalCost"))
            if cost:
                out.append(CashMoved(-cost, when))
        case "MiningRefined":
            raw, local = e.get("Type"), e.get("Type_Localised")
            if isinstance(raw, str) and raw.strip():
                m = _SYMBOL.match(raw.strip())
                symbol = (m.group(1) if m else raw).strip().lower()
                out.append(Refined(symbol, local.strip() if isinstance(local, str) else "", when))
        case "ModuleBuy":
            money(False, "ships", (_int(e.get("BuyPrice")) or 0) - (_int(e.get("SellPrice")) or 0))
        case "ShipyardBuy":
            money(False, "ships", (_int(e.get("ShipPrice")) or 0) - (_int(e.get("SellPrice")) or 0))
        case "EngineerContribution" if e.get("Type") == "Credits":
            money(False, "other", _int(e.get("Quantity")))
        case "BuyMicroResources":
            total = _int(e.get("TotalCost"))
            money(False, "other", total if total is not None else _int(e.get("Price")))
        case "SellExplorationData" | "MultiSellExplorationData":
            total = _int(e.get("TotalEarnings"))
            if total is None:
                total = (_int(e.get("BaseValue")) or 0) + (_int(e.get("Bonus")) or 0)
            money(True, "exploration", total)
            out.extend(_carried(e))
        case "SellOrganicData":
            rows = [r for r in e.get("BioData") or [] if isinstance(r, dict)]
            money(
                True,
                "exobiology",
                sum((_int(r.get("Value")) or 0) + (_int(r.get("Bonus")) or 0) for r in rows),
            )
            out.extend(_carried(e))
        case "LoadGame":
            out.append(SessionStarted(when))
            credits = _int(e.get("Credits"))
            if credits is not None:
                out.append(BalanceKnown(credits, when))
        case "Shutdown":
            out.append(SessionEnded(when))
        case "Scan":
            address, system = e.get("SystemAddress"), e.get("StarSystem")
            if (
                e.get("ScanType") != "NavBeaconDetail"
                and isinstance(address, int)
                and isinstance(system, str)
            ):
                body = _scanned(e, address)
                if body is not None:
                    out.append(DataScanned(system, body, when))
        case "FSSDiscoveryScan":
            address, system, count = e.get("SystemAddress"), e.get("SystemName"), _int(e.get("BodyCount"))
            if isinstance(address, int) and isinstance(system, str) and count:
                out.append(DataSurveyed(system, address, count, when))
        case "SAAScanComplete":
            address, body = e.get("SystemAddress"), e.get("BodyID")
            used, target = _int(e.get("ProbesUsed")), _int(e.get("EfficiencyTarget"))
            if isinstance(address, int) and isinstance(body, int):
                efficient = used is not None and target is not None and used <= target
                out.append(DataMapped(address, body, efficient, when))
        case "ScanOrganic" | "Died":
            out.extend(_carried(e))
    return out


def _carried(e: dict[str, Any]) -> list[MoneyEvent]:
    """A sale, an analysed species or a death, as the explored store reads them."""
    event = _explored(e)
    return [event] if isinstance(event, SpeciesAnalysed | ExplorationSold | OrganicSold | Died) else []


# --- missions: what is open, where, until when ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MissionTaken:
    """MissionAccepted. `system` / `station` = where it sends you ("" when it does not say: a donation is
    handed in where you took it); `expiry` "" = no time limit."""

    mission_id: int
    code: str  # the game's internal name, e.g. "Mission_PassengerBulk_BUSINESS_ARRIVING"
    title: str  # the game's own line, e.g. "12 Business Persons Seeking Transport"
    faction: str
    system: str
    station: str
    expiry: str
    reward: int
    wing: bool
    when: str


@dataclass(frozen=True, slots=True)
class MissionEnded:
    """MissionCompleted / MissionFailed / MissionAbandoned: no longer open."""

    mission_id: int
    how: str  # "completed" / "failed" / "abandoned"
    when: str
    faction: str = ""  # who gave the mission (missions done per faction)


@dataclass(frozen=True, slots=True)
class MissionRedirected:
    """MissionRedirected: the job is done, the reward waits at `system` / `station`."""

    mission_id: int
    system: str
    station: str
    when: str


@dataclass(frozen=True, slots=True)
class MissionsListed:
    """Missions (at login): the open missions, each with the seconds it has left."""

    active: tuple[tuple[int, str, int | None], ...]  # (id, code, seconds left)
    when: str


@dataclass(frozen=True, slots=True)
class DockedAt:
    """Docked (or a Location / login docked): where a mission taken now was taken."""

    system: str
    station: str
    when: str


MissionEvent = MissionTaken | MissionEnded | MissionRedirected | MissionsListed | DockedAt

_ENDED = {"MissionCompleted": "completed", "MissionFailed": "failed", "MissionAbandoned": "abandoned"}

# The mission end that counts for a faction: `edmultitool.history` pre-filters standings on it.
MISSION_DONE_EVENT = "MissionCompleted"

# The journal events that open, move or close a mission; `edmultitool.history` pre-filters on these.
MISSION_EVENTS = ("MissionAccepted", *_ENDED, "MissionRedirected", "Missions", "Docked", "Location")


def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _iso_time(v: Any) -> str:
    """A journal time ("2026-10-13T15:00:00Z") as given; "" when it is not one."""
    text = _text(v)
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        return ""
    return text if t.tzinfo is not None else ""  # compared with UTC times: a time without a zone is not one


def parse_mission(e: dict[str, Any]) -> MissionEvent | None:
    """One journal entry → what it says about your open missions; None when nothing."""
    when = entry_time(e)
    name = e.get("event")
    mission_id = e.get("MissionID")
    if not when or not isinstance(name, str):
        return None
    if name == "Missions":
        active = []
        for row in e.get("Active") or []:
            if isinstance(row, dict) and type(row.get("MissionID")) is int:
                active.append((row["MissionID"], _text(row.get("Name")), _int(row.get("Expires"))))
        return MissionsListed(tuple(active), when)
    if name in ("Docked", "Location"):
        if name == "Location" and not e.get("Docked"):
            return None
        station = _text(e.get("StationName"))
        return DockedAt(_text(e.get("StarSystem")), station, when) if station else None
    if type(mission_id) is not int:
        return None
    if name == "MissionAccepted":
        return MissionTaken(
            mission_id=mission_id,
            code=_text(e.get("Name")),
            title=_text(e.get("LocalisedName")),
            faction=_text(e.get("Faction")),
            system=_text(e.get("DestinationSystem")),
            station=_text(e.get("DestinationStation")) or _text(e.get("DestinationSettlement")),
            expiry=_iso_time(e.get("Expiry")),
            reward=_int(e.get("Reward")) or 0,
            wing=e.get("Wing") is True,
            when=when,
        )
    if name in _ENDED:
        return MissionEnded(mission_id, _ENDED[name], when, _text(e.get("Faction")))
    if name == "MissionRedirected":
        return MissionRedirected(
            mission_id, _text(e.get("NewDestinationSystem")), _text(e.get("NewDestinationStation")), when
        )
    return None


# --- Statistics: the game's own lifetime totals, written once per login -----------------------------


@dataclass(frozen=True, slots=True)
class StatValue:
    """One lifetime total from a Statistics entry, under our own names (the journal's stay here)."""

    group: str  # our card: "explore", "exobio", "spent", … (unknown game groups: their name in lower case)
    key: str  # our name for figures we use ("jumps", "explore_profit"…); "group.field" for the rest
    label: str  # plain English
    unit: str  # "cr" | "ly" | "m" | "s" | "" (a count)
    value: float


@dataclass(frozen=True, slots=True)
class StatisticsKnown:
    values: tuple[StatValue, ...]
    when: str


# the game's groups -> our card and its title; a group not listed keeps its own name
STAT_GROUPS: dict[str, tuple[str, str]] = {
    "Bank_Account": ("bank", "Other currencies"),
    "Combat": ("combat", "Combat"),
    "Crime": ("crime", "Crime"),
    "Smuggling": ("smuggling", "Smuggling"),
    "Trading": ("trade", "Trading"),
    "Mining": ("mining", "Mining"),
    "Exploration": ("explore", "Travel and exploration"),
    "Passengers": ("passengers", "Passengers"),
    "Search_And_Rescue": ("rescue", "Search and rescue"),
    "Squadron": ("squadron", "Squadron"),
    "Crafting": ("crafting", "Engineering"),
    "Crew": ("crew", "Crew"),
    "Multicrew": ("multicrew", "Multicrew"),
    "Material_Trader_Stats": ("materials", "Material traders"),
    "Exobiology": ("exobio", "Exobiology"),
    "CQC": ("cqc", "CQC"),
    "TG_ENCOUNTERS": ("thargoid", "Thargoid encounters"),
    "FLEETCARRIER": ("carrier", "Fleet carrier"),
}
# figures we name ourselves: (game group, field) -> (our card, our key, label, unit); the rest are labelled
# from the field's own name, unit guessed from it
STAT_FIELDS: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("Bank_Account", "Current_Wealth"): ("overview", "wealth", "Wealth (credits and ships)", "cr"),
    ("Bank_Account", "Owned_Ship_Count"): ("overview", "ships_owned", "Ships owned", ""),
    ("Bank_Account", "Suits_Owned"): ("overview", "suits_owned", "Suits owned", ""),
    ("Bank_Account", "Weapons_Owned"): ("overview", "weapons_owned", "Personal weapons owned", ""),
    ("Exploration", "Time_Played"): ("overview", "time_played", "Time played", "s"),
    ("Bank_Account", "Spent_On_Ships"): ("spent", "spent_ships", "Ships", "cr"),
    ("Bank_Account", "Spent_On_Outfitting"): ("spent", "spent_outfitting", "Outfitting", "cr"),
    ("Bank_Account", "Spent_On_Repairs"): ("spent", "spent_repairs", "Repairs", "cr"),
    ("Bank_Account", "Spent_On_Fuel"): ("spent", "spent_fuel", "Fuel", "cr"),
    ("Bank_Account", "Spent_On_Ammo_Consumables"): ("spent", "spent_ammo", "Ammunition and limpets", "cr"),
    ("Bank_Account", "Insurance_Claims"): ("spent", "insurance_claims", "Insurance claims (rebuys)", ""),
    ("Bank_Account", "Spent_On_Insurance"): ("spent", "spent_insurance", "Insurance (rebuys)", "cr"),
    ("Bank_Account", "Spent_On_Suits"): ("spent", "spent_suits", "Suits", "cr"),
    ("Bank_Account", "Spent_On_Weapons"): ("spent", "spent_weapons", "Personal weapons", "cr"),
    ("Bank_Account", "Spent_On_Suit_Consumables"): (
        "spent",
        "spent_suit_consumables",
        "Suit consumables",
        "cr",
    ),
    ("Bank_Account", "Spent_On_Premium_Stock"): ("spent", "spent_premium", "Premium ammunition", "cr"),
    ("Bank_Account", "Premium_Stock_Bought"): ("spent", "premium_bought", "Premium ammunition bought", ""),
    ("Exploration", "Spent_On_Shuttles"): ("spent", "spent_shuttles", "Shuttles", "cr"),
    ("Crew", "NpcCrew_TotalWages"): ("spent", "crew_wages", "Crew wages", "cr"),
    ("Combat", "Bounties_Claimed"): ("combat", "bounties_claimed", "Bounties claimed", ""),
    ("Combat", "Bounty_Hunting_Profit"): ("combat", "bounty_profit", "Bounty hunting profit", "cr"),
    ("Combat", "Combat_Bonds"): ("combat", "bonds", "Combat bonds", ""),
    ("Combat", "Combat_Bond_Profits"): ("combat", "bond_profit", "Combat bond profit", "cr"),
    ("Combat", "Assassinations"): ("combat", "assassinations", "Assassinations", ""),
    ("Combat", "Assassination_Profits"): ("combat", "assassination_profit", "Assassination profit", "cr"),
    ("Combat", "Highest_Single_Reward"): ("combat", "combat.highest", "Highest single reward", "cr"),
    ("Combat", "OnFoot_Combat_Bonds_Profits"): (
        "combat",
        "foot_bond_profit",
        "On-foot combat bond profit",
        "cr",
    ),
    ("Crime", "Notoriety"): ("crime", "crime.notoriety", "Notoriety now", ""),
    ("Crime", "Fines"): ("crime", "crime.fines", "Fines", ""),
    ("Crime", "Total_Fines"): ("crime", "crime.total_fines", "Fines in all", "cr"),
    ("Crime", "Bounties_Received"): ("crime", "crime.bounties", "Bounties on you", ""),
    ("Crime", "Total_Bounties"): ("crime", "crime.total_bounties", "Bounties on you in all", "cr"),
    ("Crime", "Highest_Bounty"): ("crime", "crime.highest", "Highest bounty on you", "cr"),
    ("Smuggling", "Black_Markets_Traded_With"): (
        "smuggling",
        "smuggling.markets",
        "Black markets traded with",
        "",
    ),
    ("Smuggling", "Black_Markets_Profits"): ("smuggling", "smuggling_profit", "Black market profit", "cr"),
    ("Smuggling", "Resources_Smuggled"): ("smuggling", "smuggling.tonnes", "Tonnes smuggled", ""),
    ("Trading", "Markets_Traded_With"): ("trade", "trade.markets", "Markets traded with", ""),
    ("Trading", "Market_Profits"): ("trade", "trade_profit", "Trade profit", "cr"),
    ("Trading", "Resources_Traded"): ("trade", "trade.tonnes", "Tonnes traded", ""),
    ("Trading", "Average_Profit"): ("trade", "trade.average", "Average profit per sale", "cr"),
    ("Trading", "Highest_Single_Transaction"): ("trade", "trade.highest", "Best single sale", "cr"),
    ("Mining", "Mining_Profits"): ("mining", "mining_profit", "Mining profit", "cr"),
    ("Mining", "Quantity_Mined"): ("mining", "mining.tonnes", "Tonnes mined", ""),
    ("Mining", "Materials_Collected"): ("mining", "mining.materials", "Materials collected", ""),
    ("Exploration", "Systems_Visited"): ("explore", "systems_visited", "Systems visited", ""),
    ("Exploration", "Exploration_Profits"): ("explore", "explore_profit", "Exploration data sold", "cr"),
    ("Exploration", "Planets_Scanned_To_Level_2"): ("explore", "explore.level2", "Bodies scanned (FSS)", ""),
    ("Exploration", "Planets_Scanned_To_Level_3"): (
        "explore",
        "explore.level3",
        "Bodies scanned in detail",
        "",
    ),
    ("Exploration", "Efficient_Scans"): (
        "explore",
        "explore.efficient",
        "Mapped within the probe target",
        "",
    ),
    ("Exploration", "Highest_Payout"): ("explore", "explore.highest", "Best single sale", "cr"),
    ("Exploration", "Total_Hyperspace_Distance"): ("explore", "distance", "Distance jumped", "ly"),
    ("Exploration", "Total_Hyperspace_Jumps"): ("explore", "jumps", "Hyperspace jumps", ""),
    ("Exploration", "Greatest_Distance_From_Start"): (
        "explore",
        "explore.farthest",
        "Farthest from where you started",
        "ly",
    ),
    ("Exploration", "OnFoot_Distance_Travelled"): ("explore", "explore.on_foot", "Walked on foot", "m"),
    ("Exploration", "Shuttle_Journeys"): ("explore", "explore.shuttles", "Shuttle journeys", ""),
    ("Exploration", "Shuttle_Distance_Travelled"): (
        "explore",
        "explore.shuttle_ly",
        "Distance by shuttle",
        "ly",
    ),
    ("Exploration", "First_Footfalls"): ("explore", "explore.first_footfalls", "First footfalls", ""),
    ("Exploration", "Planet_Footfalls"): ("explore", "explore.footfalls", "Planets walked on", ""),
    ("Exploration", "Settlements_Visited"): ("explore", "explore.settlements", "Settlements visited", ""),
    ("Passengers", "Passengers_Missions_Accepted"): (
        "passengers",
        "passengers.accepted",
        "Missions accepted",
        "",
    ),
    ("Passengers", "Passengers_Missions_Delivered"): (
        "passengers",
        "passengers.delivered",
        "Passengers delivered",
        "",
    ),
    ("Passengers", "Passengers_Missions_Bulk"): ("passengers", "passengers.bulk", "Bulk passengers", ""),
    ("Passengers", "Passengers_Missions_VIP"): ("passengers", "passengers.vip", "VIP passengers", ""),
    ("Passengers", "Passengers_Missions_Ejected"): (
        "passengers",
        "passengers.ejected",
        "Passengers ejected",
        "",
    ),
    ("Search_And_Rescue", "SearchRescue_Profit"): (
        "rescue",
        "rescue_profit",
        "Search and rescue profit",
        "cr",
    ),
    ("Search_And_Rescue", "SearchRescue_Count"): ("rescue", "rescue.count", "Items handed in", ""),
    ("Search_And_Rescue", "SearchRescue_Traded"): ("rescue", "rescue.traded", "Tonnes handed in", ""),
    ("Multicrew", "Multicrew_Credits_Total"): (
        "multicrew",
        "multicrew_profit",
        "Credits earned as crew",
        "cr",
    ),
    ("Exobiology", "Organic_Genus_Encountered"): ("exobio", "exobio.genus_seen", "Genera encountered", ""),
    ("Exobiology", "Organic_Species_Encountered"): (
        "exobio",
        "exobio.species_seen",
        "Species encountered",
        "",
    ),
    ("Exobiology", "Organic_Variant_Encountered"): (
        "exobio",
        "exobio.variants_seen",
        "Variants encountered",
        "",
    ),
    ("Exobiology", "Organic_Data_Profits"): ("exobio", "exobio_profit", "Organic data sold", "cr"),
    ("Exobiology", "Organic_Data"): ("exobio", "organic_data", "Samples sold", ""),
    ("Exobiology", "First_Logged_Profits"): ("exobio", "exobio.first_profit", "First-logged bonus", "cr"),
    ("Exobiology", "First_Logged"): ("exobio", "exobio.first", "Species logged first", ""),
    ("Exobiology", "Organic_Systems"): ("exobio", "exobio.systems", "Systems with samples", ""),
    ("Exobiology", "Organic_Planets"): ("exobio", "exobio.planets", "Planets with samples", ""),
    ("Exobiology", "Organic_Genus"): ("exobio", "exobio.genus", "Genera sampled", ""),
    ("Exobiology", "Organic_Species"): ("exobio", "exobio.species", "Species sampled", ""),
    ("CQC", "CQC_KD"): ("cqc", "cqc.kd", "Kills per death", "ratio"),
    ("CQC", "CQC_WL"): ("cqc", "cqc.wl", "Wins per loss", "ratio"),
    ("Multicrew", "Multicrew_Fines_Total"): ("multicrew", "multicrew.fines", "Fines as crew", "cr"),
}
_CREDIT_WORDS = (
    "profit", "spent", "spend", "wealth", "credits", "value", "payout", "reward", "transaction", "wages",
    "fines", "maintenance", "cost", "price",
)  # fmt: skip
_LY_TEXT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*LY\s*$", re.IGNORECASE)  # a distance the game writes as text

# The journal event `edmultitool.history` pre-filters on.
STATISTICS_EVENTS = ("Statistics",)


def _stat_unit(field: str) -> str:
    """A unit guessed from a field's name, for figures not named in STAT_FIELDS."""
    f = field.lower()
    if f.startswith("merccoins") or f.endswith("_num") or f.endswith("_count"):
        return ""
    if "time" in f:
        return "s"
    if "distance" in f:
        return "m" if "onfoot" in f or "on_foot" in f else "ly"
    if any(word in f for word in _CREDIT_WORDS):
        return "cr"
    return ""


_STAT_PREFIXES = ("Passengers_Missions_", "NpcCrew_", "SearchRescue_", "Squadron_", "Multicrew_")
_STAT_WORDS = {
    "vip": "VIP", "securityforces": "security forces", "onfoot": "on foot", "npc": "NPC",
    "conflictzone": "conflict zone", "poi": "POI", "merccoins": "Merc coins",
    "personalassets": "personal assets", "highestcontribution": "highest contribution", "num": "(number)",
    "cqc": "CQC", "tg": "Thargoid", "fleetcarrier": "", "operationscore": "operation score", "bgs": "BGS",
    "searchrescue": "search and rescue",
}  # fmt: skip


def _stat_label(field: str) -> str:
    """A plain label from a field's own name: "Passengers_Missions_Tourist_vip_delivered" → "Tourist VIP
    delivered"."""
    for prefix in _STAT_PREFIXES:
        if field.startswith(prefix) and len(field) > len(prefix):
            field = field[len(prefix) :]
            break
    words = " ".join(word for w in field.split("_") if w and (word := _STAT_WORDS.get(w.lower(), w.lower())))
    return words[:1].upper() + words[1:]


def parse_statistics(e: dict[str, Any]) -> StatisticsKnown | None:
    """A Statistics entry → every number in it under our own names; None for any other entry. A group or field
    the game adds later is kept, labelled from its own name."""
    when = entry_time(e)
    if e.get("event") != "Statistics" or not when:
        return None
    values = []
    for group, fields in e.items():
        if not isinstance(fields, dict):
            continue
        card = STAT_GROUPS.get(group, (group.lower(), ""))[0]
        for field, raw in fields.items():
            if not isinstance(field, str):
                continue
            unit = ""
            if isinstance(raw, str) and (m := _LY_TEXT.match(raw)):
                raw, unit = float(m.group(1)), "ly"  # "4170 LY"
            if not isinstance(raw, int | float) or isinstance(raw, bool):
                continue
            known = STAT_FIELDS.get((group, field))
            if known is not None:
                values.append(StatValue(known[0], known[1], known[2], known[3], float(raw)))
            else:
                key = f"{card}.{field.lower()}"
                unit = unit or _stat_unit(field)
                values.append(StatValue(card, key, _stat_label(field), unit, float(raw)))
    return StatisticsKnown(tuple(values), when)


def stat_group_title(card: str) -> str:
    """The title of one of our stat cards ("" when the game's own group name is all we have)."""
    return next((title for key, title in STAT_GROUPS.values() if key == card), "")
